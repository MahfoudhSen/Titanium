"""Pandas pipeline that turns messy bank exports into clean, uniform transactions.

Handles: different column names per bank, junk preamble rows, mixed date formats,
'$1,234.56' / '(45.00)' / '45.00 CR' amounts, split Debit/Credit columns,
noisy merchant strings ('SQ *BLUE BOTTLE #123 SAN FRANCISCO CA'), and duplicates.
"""
from __future__ import annotations

import csv
import hashlib
import io
import re

import pandas as pd

DATE_ALIASES = ["date", "transaction date", "trans date", "posted date", "posting date", "post date"]
DESC_ALIASES = ["description", "desc", "merchant", "payee", "name", "details", "memo", "transaction"]
AMOUNT_ALIASES = ["amount", "transaction amount", "amt", "value"]
DEBIT_ALIASES = ["debit", "withdrawal", "withdrawals", "money out", "debit amount"]
CREDIT_ALIASES = ["credit", "deposit", "deposits", "money in", "credit amount"]

DATE_FORMATS = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%d-%b-%Y", "%b %d, %Y", "%Y/%m/%d", "%m-%d-%Y", "%d %b %Y"]


class PipelineError(ValueError):
    pass


# ---------------------------------------------------------------- reading
def read_export(content: bytes, filename: str = "") -> pd.DataFrame:
    """Read CSV/XLSX, skipping any preamble lines banks put above the header."""
    if filename.lower().endswith((".xlsx", ".xls")):
        raw = pd.read_excel(io.BytesIO(content), header=None, dtype=str)
    else:
        text = content.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
        if not rows:
            raise PipelineError("File is empty.")
        width = max(len(r) for r in rows)  # banks add ragged preamble/summary rows
        raw = pd.DataFrame([r + [""] * (width - len(r)) for r in rows], dtype=str)

    header_idx = _find_header_row(raw)
    df = raw.iloc[header_idx + 1:].copy()
    df.columns = [str(c).strip().lower() or f"col_{i}" for i, c in enumerate(raw.iloc[header_idx])]
    return df.reset_index(drop=True)


def _find_header_row(raw: pd.DataFrame) -> int:
    known = set(DATE_ALIASES + DESC_ALIASES + AMOUNT_ALIASES + DEBIT_ALIASES + CREDIT_ALIASES)
    for i in range(min(len(raw), 25)):
        cells = {str(c).strip().lower() for c in raw.iloc[i].tolist()}
        if len(cells & known) >= 2:
            return i
    raise PipelineError("Couldn't find a header row with date/description/amount columns.")


def _pick(cols: list[str], aliases: list[str]) -> str | None:
    for a in aliases:
        if a in cols:
            return a
    for a in aliases:  # fuzzy: 'transaction date (posted)'
        for c in cols:
            if a in c:
                return c
    return None


# ---------------------------------------------------------------- parsing
def parse_amount(val) -> float | None:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    if not s or s.lower() in {"nan", "none", "-"}:
        return None
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    up = s.upper()
    if up.endswith("CR"):
        s = s[:-2]
    elif up.endswith("DR"):
        neg, s = True, s[:-2]
    s = re.sub(r"[^\d.\-]", "", s)
    if s.startswith("-"):
        neg, s = not neg, s[1:]
    if s.count(".") > 1 or not s.replace(".", ""):
        return None
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def parse_dates(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.strip()
    out = pd.Series(pd.NaT, index=s.index)
    for fmt in DATE_FORMATS:
        missing = out.isna()
        if not missing.any():
            break
        out[missing] = pd.to_datetime(s[missing], format=fmt, errors="coerce")
    missing = out.isna()
    if missing.any():  # last resort
        out[missing] = pd.to_datetime(s[missing], errors="coerce")
    return out


_NOISE_PREFIXES = re.compile(
    r"^(POS PURCHASE|POS DEBIT|POS|DEBIT CARD PURCHASE|DEBIT CARD|CHECKCARD|CHECK CARD|PURCHASE AUTHORIZED ON \d{1,2}/\d{1,2}|"
    r"PURCHASE|RECURRING PAYMENT|ACH DEBIT|ACH CREDIT|ACH|ONLINE PAYMENT|PREAUTHORIZED DEBIT|VISA|"
    r"SQ \*|SQ\*|TST\* ?|TST \*|PP\*|PAYPAL \*|SP \*|SP\*|DD \*|IC\* ?|PY \*|EB \*)\s*",
    re.I,
)
_US_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
              "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()

_CITY_PREFIXES = set("SAN LOS LAS NEW SANTA EL FORT FT ST PALO SALT LAKE MOUNTAIN REDWOOD SOUTH NORTH "
                     "WEST EAST PALM LONG BATON CORPUS GRAND COLORADO JERSEY KANSAS OKLAHOMA SIOUX".split())

MERCHANT_ALIASES = {  # canonical names for common messy variants
    r"AMZN|AMAZON": "Amazon",
    r"UBER\s*\*?\s*EATS": "Uber Eats",
    r"^UBER": "Uber",
    r"LYFT": "Lyft",
    r"STARBUCKS|SBUX": "Starbucks",
    r"NETFLIX": "Netflix",
    r"SPOTIFY": "Spotify",
    r"WHOLE ?FOODS|WHOLEFDS|WFM\b": "Whole Foods",
    r"TRADER JOE": "Trader Joe's",
    r"WAL-?MART|WM SUPERCENTER": "Walmart",
    r"TARGET": "Target",
    r"COSTCO": "Costco",
    r"SHELL OIL|^SHELL": "Shell",
    r"CHEVRON": "Chevron",
    r"DOORDASH": "DoorDash",
    r"APPLE\.COM|APPLE COM BILL|ITUNES": "Apple",
    r"GOOGLE \*|GOOGLE\*": "Google",
    r"VENMO": "Venmo",
    r"ZELLE": "Zelle",
    r"COMCAST|XFINITY": "Comcast Xfinity",
}


def normalize_merchant(desc: str) -> str:
    s = re.sub(r"\s+", " ", str(desc)).strip().upper()
    for _ in range(3):
        s = _NOISE_PREFIXES.sub("", s).strip()
    for pattern, name in MERCHANT_ALIASES.items():
        if re.search(pattern, s):
            return name
    s = re.sub(r"\b(PPD|WEB|CCD|TEL|ARC)\s*ID:?.*$", " ", s)            # ACH trailers
    s = re.sub(r"\b\d{3}-[\dA-Z]{3}-[\dA-Z]{4}\b", " ", s)               # phone numbers
    s = re.sub(r"\b\d{1,2}/\d{1,2}(/\d{2,4})?\b", " ", s)           # embedded dates
    s = re.sub(r"\b(X{2,}|\*{2,})\d+\b", " ", s)                     # masked card numbers
    s = re.sub(r"#\s*\d+|\bSTORE\s*\d+|\b\d{3,}\b", " ", s)          # store / ref numbers
    s = re.sub(r"\b[\w.-]+\.(COM|NET|ORG)\b", lambda m: m.group(0).split(".")[0], s)
    s = re.sub(r"[*#]", " ", s)
    tokens = s.split()
    if len(tokens) > 1 and tokens[-1] in _US_STATES:                  # trailing 'CITY ST'
        tokens = tokens[:-1]
        if len(tokens) > 1:
            tokens = tokens[:-1]
            while len(tokens) > 1 and tokens[-1] in _CITY_PREFIXES:   # 'SAN FRANCISCO'
                tokens = tokens[:-1]
    s = " ".join(tokens).strip(" -.,")
    return s.title() if s else "Unknown"


def fingerprint(date, desc: str, amount: float, occurrence: int) -> str:
    norm = re.sub(r"\s+", " ", desc.strip().upper())
    key = f"{date}|{norm}|{amount:.2f}|{occurrence}"
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------- main
def clean(df: pd.DataFrame) -> pd.DataFrame:
    """Return columns: date, raw_description, merchant, amount, fingerprint."""
    cols = list(df.columns)
    date_col = _pick(cols, DATE_ALIASES)
    desc_col = _pick(cols, DESC_ALIASES)
    amt_col = _pick(cols, AMOUNT_ALIASES)
    debit_col = _pick(cols, DEBIT_ALIASES)
    credit_col = _pick(cols, CREDIT_ALIASES)

    if not date_col or not desc_col or not (amt_col or debit_col or credit_col):
        raise PipelineError(f"Missing required columns. Found: {cols}")

    out = pd.DataFrame()
    out["date"] = parse_dates(df[date_col])
    out["raw_description"] = df[desc_col].fillna("").astype(str).str.strip()

    if amt_col:
        out["amount"] = df[amt_col].map(parse_amount)
        # Some banks export a separate type column ('Debit'/'Credit') with positive amounts
        type_col = _pick(cols, ["type", "transaction type", "dr/cr"])
        if type_col and (out["amount"].dropna() >= 0).all():
            is_debit = df[type_col].astype(str).str.lower().str.contains("debit|dr|withdraw|purchase|sale")
            out.loc[is_debit, "amount"] = -out.loc[is_debit, "amount"].abs()
    else:
        debit = df[debit_col].map(parse_amount).abs() if debit_col else 0
        credit = df[credit_col].map(parse_amount).abs() if credit_col else 0
        out["amount"] = pd.Series(credit).fillna(0) - pd.Series(debit).fillna(0)
        both_empty = (df[debit_col].map(parse_amount).isna() if debit_col else True) & \
                     (df[credit_col].map(parse_amount).isna() if credit_col else True)
        out.loc[both_empty, "amount"] = None

    # drop junk: footer totals, blank lines, pending rows w/o amount
    out = out.dropna(subset=["date", "amount"])
    out = out[out["raw_description"].str.len() > 0]
    out = out[~out["raw_description"].str.contains(r"^(?:beginning|ending|total|opening|closing) balance", case=False)]

    out["date"] = out["date"].dt.date
    out["amount"] = out["amount"].round(2)
    out["merchant"] = out["raw_description"].map(normalize_merchant)

    # Identical rows on the same day are legit (two coffees) -> number occurrences
    out["_occ"] = out.groupby(["date", "raw_description", "amount"]).cumcount()
    out["fingerprint"] = [fingerprint(d, r, a, o) for d, r, a, o in
                          zip(out["date"], out["raw_description"], out["amount"], out["_occ"])]
    return out.drop(columns="_occ").sort_values("date").reset_index(drop=True)
