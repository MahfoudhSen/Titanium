"""Transaction classification + unusual-charge detection.

1. Cached merchant -> category mappings (includes user corrections) are reused.
2. New merchants are sent to an LLM in batches; it returns JSON category labels
   and can flag merchants that look suspicious (e.g. unknown foreign charges).
3. If no API key is configured, a keyword rule engine is used instead, so the
   app works fully offline.
4. Statistical anomaly detection flags charges far above the merchant's or
   category's typical amount, first-time large charges, and duplicate charges.
"""
from __future__ import annotations

import json
import logging
import os
import re

import pandas as pd

log = logging.getLogger("titanium.classifier")

CATEGORIES = [
    "Groceries", "Dining", "Coffee", "Transport", "Fuel", "Shopping", "Subscriptions",
    "Utilities", "Rent & Housing", "Health", "Entertainment", "Travel", "Income",
    "Transfers", "Fees", "Insurance", "Education", "Other",
]

RULES: list[tuple[str, str]] = [
    (r"payroll|direct dep|salary|paycheck|\badp\b|gusto|interest paid|dividend", "Income"),
    (r"venmo|zelle|transfer|xfer|cash app|wire|atm|withdrawal|payment thank you|autopay|credit card payment", "Transfers"),
    (r"starbucks|dunkin|peet|blue bottle|coffee|cafe|espresso|philz", "Coffee"),
    (r"whole foods|trader joe|safeway|kroger|aldi|publix|grocery|market|wegmans|h-e-b|heb|sprouts|costco|instacart", "Groceries"),
    (r"uber eats|doordash|grubhub|postmates|restaurant|pizza|burger|taco|sushi|chipotle|mcdonald|kitchen|grill|bar\b|diner|bistro|thai|ramen|sweetgreen|shake shack|wendy|subway|chick-fil", "Dining"),
    (r"netflix|spotify|hulu|disney|hbo|max\b|youtube|apple|icloud|google|adobe|dropbox|openai|chatgpt|anthropic|claude|patreon|substack|prime video|audible|github", "Subscriptions"),
    (r"^uber$|^uber |lyft|mta|bart|metro|transit|parking|toll|clipper|amtrak|caltrain|citi bike", "Transport"),
    (r"shell|chevron|exxon|mobil|bp\b|arco|76\b|valero|sunoco|gas station|fuel", "Fuel"),
    (r"comcast|xfinity|verizon|at&t|att\b|t-mobile|pg&e|pge|con ed|electric|water|internet|spectrum", "Utilities"),
    (r"rent|apartment|property|mortgage|hoa|landlord|zillow", "Rent & Housing"),
    (r"cvs|walgreens|pharmacy|rite aid|dental|doctor|clinic|hospital|medical|gym|fitness|equinox|planet fitness|peloton", "Health"),
    (r"amc|cinema|theater|ticketmaster|steam|playstation|xbox|nintendo|concert|stubhub|eventbrite", "Entertainment"),
    (r"airline|delta|united|american air|southwest|jetblue|airbnb|hotel|marriott|hilton|hyatt|expedia|booking\.com", "Travel"),
    (r"fee|overdraft|service charge|late charge|interest charge|foreign transaction", "Fees"),
    (r"geico|state farm|progressive|allstate|insurance|lemonade", "Insurance"),
    (r"tuition|university|college|coursera|udemy|school", "Education"),
    (r"amazon|target|walmart|best buy|ikea|etsy|ebay|nike|zara|h&m|uniqlo|home depot|lowe", "Shopping"),
]


def rule_category(merchant: str, raw: str, amount: float) -> str:
    text = f"{merchant} {raw}".lower()
    for pattern, cat in RULES:
        if re.search(pattern, text):
            if cat not in ("Income", "Transfers") and amount > 0 and not re.search(r"refund|return|reversal|credit", text):
                continue  # money in for a spend category = probably not that category
            return cat
    return "Income" if amount > 0 else "Other"


# ------------------------------------------------------------------ LLM
LLM_PROMPT = """You categorize bank transactions for a personal finance dashboard.

Allowed categories: {categories}

For each merchant below, return its most likely category. Also set "suspicious": true only if the
merchant itself looks like a likely scam, unrecognizable foreign/crypto charge, or something a typical
cardholder would want to double-check; otherwise false. Give a short "reason" only when suspicious.

Merchants (with a sample raw bank description and sample amount; negative = money out):
{items}

Respond with ONLY a JSON array, no prose, like:
[{{"merchant": "...", "category": "...", "suspicious": false, "reason": ""}}]"""


def _llm_backend() -> str | None:
    if os.getenv("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.getenv("OPENAI_API_KEY"):
        return "openai"
    return None


def _call_llm(prompt: str) -> str:
    backend = _llm_backend()
    if backend == "anthropic":
        import anthropic
        client = anthropic.Anthropic()
        msg = client.messages.create(
            model=os.getenv("LLM_MODEL", "claude-haiku-5-5"),
            max_tokens=4000,
            messages=[{"role": "user", "content": prompt}],
        )
        return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    if backend == "openai":
        import httpx
        r = httpx.post(
            os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1") + "/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={"model": os.getenv("LLM_MODEL", "gpt-4o-mini"), "temperature": 0,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]
    raise RuntimeError("No LLM configured")


def _parse_json_array(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text, re.S)
    if not m:
        raise ValueError("LLM returned no JSON array")
    return json.loads(m.group(0))


def llm_classify(samples: list[dict], batch_size: int = 40) -> dict[str, dict]:
    """samples: [{merchant, raw, amount}] -> {merchant: {category, suspicious, reason}}"""
    results: dict[str, dict] = {}
    for i in range(0, len(samples), batch_size):
        batch = samples[i:i + batch_size]
        items = "\n".join(f'- "{s["merchant"]}" | raw: "{s["raw"][:80]}" | amount: {s["amount"]:.2f}' for s in batch)
        prompt = LLM_PROMPT.format(categories=", ".join(CATEGORIES), items=items)
        try:
            for row in _parse_json_array(_call_llm(prompt)):
                cat = row.get("category", "Other")
                results[row.get("merchant", "")] = {
                    "category": cat if cat in CATEGORIES else "Other",
                    "suspicious": bool(row.get("suspicious")),
                    "reason": (row.get("reason") or "")[:200],
                }
        except Exception as e:  # never let the LLM break an upload
            log.warning("LLM batch failed, falling back to rules: %s", e)
    return results


# ------------------------------------------------------------------ main entry
def classify(df: pd.DataFrame, cache: dict[str, str]) -> tuple[pd.DataFrame, dict[str, str]]:
    """Adds category, classified_by, llm_flag_reason. Returns (df, new_cache_entries)."""
    df = df.copy()
    df["category"] = None
    df["classified_by"] = "rules"
    df["llm_flag_reason"] = None
    new_entries: dict[str, str] = {}

    cached = df["merchant"].isin(cache.keys())
    df.loc[cached, "category"] = df.loc[cached, "merchant"].map(cache)
    df.loc[cached, "classified_by"] = "cache"

    unknown = df.loc[~cached].drop_duplicates("merchant")
    if len(unknown) and _llm_backend():
        samples = [{"merchant": r.merchant, "raw": r.raw_description, "amount": r.amount}
                   for r in unknown.itertuples()]
        labels = llm_classify(samples)
        for merchant, lab in labels.items():
            mask = (df["merchant"] == merchant) & df["category"].isna()
            df.loc[mask, "category"] = lab["category"]
            df.loc[mask, "classified_by"] = "llm"
            if lab["suspicious"]:
                df.loc[mask, "llm_flag_reason"] = f"AI: {lab['reason'] or 'unrecognized merchant'}"
            new_entries[merchant] = lab["category"]

    rest = df["category"].isna()
    df.loc[rest, "category"] = [rule_category(m, r, a) for m, r, a in
                                zip(df.loc[rest, "merchant"], df.loc[rest, "raw_description"], df.loc[rest, "amount"])]
    return df, new_entries


def detect_anomalies(df: pd.DataFrame, history: pd.DataFrame | None = None) -> pd.DataFrame:
    """Flags unusual charges. `history` = previously stored transactions for context."""
    df = df.copy()
    reasons: list[list[str]] = [[] for _ in range(len(df))]
    full = pd.concat([history, df], ignore_index=True) if history is not None and len(history) else df
    spend = full[full["amount"] < 0].assign(abs_amt=lambda x: x["amount"].abs())

    m_stats = spend.groupby("merchant")["abs_amt"].agg(["median", "count"])
    c_stats = spend.groupby("category")["abs_amt"].agg(["median", "count", "std"])

    for i, r in enumerate(df.itertuples()):
        if r.amount >= 0 or r.category in ("Transfers", "Rent & Housing"):
            continue
        amt = abs(r.amount)
        if r.merchant in m_stats.index:
            med, n = m_stats.loc[r.merchant, ["median", "count"]]
            if n >= 3 and amt > max(3 * med, med + 50):
                reasons[i].append(f"{amt / med:.1f}x your usual ${med:,.2f} at {r.merchant}")
        if r.category in c_stats.index:
            med, n, sd = c_stats.loc[r.category, ["median", "count", "std"]]
            if n >= 5 and pd.notna(sd) and amt > med + 4 * sd and amt > 100:
                reasons[i].append(f"Unusually large {r.category} charge")
        if r.merchant in m_stats.index and m_stats.loc[r.merchant, "count"] == 1:
            if r.category == "Other" and amt >= 200:
                reasons[i].append("Large charge from an unrecognized merchant")
            elif amt >= 500:
                reasons[i].append("Large first-time charge from this merchant")
        if re.search(r"binance|crypto|coinbase|\.top\b|\.xyz\b|\.ru\b", f"{r.merchant} {r.raw_description}", re.I):
            reasons[i].append("Crypto / high-risk merchant")
        if r.category == "Fees":
            reasons[i].append("Bank fee")
        if getattr(r, "llm_flag_reason", None):
            reasons[i].append(r.llm_flag_reason)

    # Possible double-charge: same merchant + amount within 3 days
    out = df[df["amount"] < 0].sort_values("date")
    for _, g in out.groupby(["merchant", "amount"]):
        if len(g) < 2 or abs(g["amount"].iloc[0]) < 15:
            continue
        dates = pd.to_datetime(g["date"])
        for idx, gap in zip(g.index[1:], dates.diff().dt.days.iloc[1:]):
            if gap <= 3 and g.loc[idx, "category"] not in ("Coffee", "Transport", "Transfers"):
                reasons[df.index.get_loc(idx)].append("Possible duplicate charge")

    df["flag_reason"] = ["; ".join(dict.fromkeys(r)) or None for r in reasons]
    df["is_flagged"] = df["flag_reason"].notna()
    return df
