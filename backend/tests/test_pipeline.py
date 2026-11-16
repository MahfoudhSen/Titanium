import os
import pathlib

import pandas as pd
import pytest

os.environ["DATABASE_URL"] = "sqlite:///./test_titanium.db"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)

from app import pipeline  # noqa: E402
from app.classifier import classify, detect_anomalies, rule_category  # noqa: E402

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "sample_data"


@pytest.mark.parametrize("raw,expected", [
    ("$1,234.56", 1234.56), ("(45.00)", -45.0), ("($2,100.00)", -2100.0), ("-12.5", -12.5),
    ("45.00 CR", 45.0), ("45.00 DR", -45.0), ("", None), ("abc", None), ("-", None),
])
def test_parse_amount(raw, expected):
    assert pipeline.parse_amount(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("SQ *BLUE BOTTLE COFFEE #12 SAN FRANCISCO CA", "Blue Bottle Coffee"),
    ("AMZN Mktp US*2K4L19RT2 Amzn.com/bill WA", "Amazon"),
    ("UBER   *EATS PENDING help.uber.com CA", "Uber Eats"),
    ("UBER   *TRIP HELP.UBER.COM CA", "Uber"),
    ("STARBUCKS STORE 08812 OAKLAND CA", "Starbucks"),
    ("TARGET 00027541 EMERYVILLE CA", "Target"),
    ("ACME CORP PAYROLL PPD ID: 99812", "Acme Corp Payroll"),
    ("WHOLEFDS MKT #10234 BERKELEY CA", "Whole Foods"),
    ("NETFLIX.COM 866-579-7172 CA", "Netflix"),
    ("POS PURCHASE CVS/PHARMACY #09812 OAKLAND CA", "Cvs/Pharmacy"),
])
def test_normalize_merchant(raw, expected):
    assert pipeline.normalize_merchant(raw) == expected


def test_mixed_dates():
    s = pd.Series(["09/01/2026", "09/02/26", "2026-09-03", "04-Sep-2026", "Sep 05, 2026"])
    assert [d.day for d in pipeline.parse_dates(s)] == [1, 2, 3, 4, 5]


@pytest.mark.parametrize("name", ["chase_checking_aug_sep.csv", "bofa_export_sep_oct.csv",
                                  "capitalone_card_oct_nov.csv"])
def test_real_world_exports(name):
    df = pipeline.clean(pipeline.read_export((SAMPLES / name).read_bytes(), name))
    assert len(df) > 50
    assert df["amount"].notna().all() and df["date"].notna().all()
    assert (df["amount"] < 0).sum() > (df["amount"] > 0).sum()   # mostly spending
    assert not df["raw_description"].str.contains("balance", case=False).any()


def test_debit_credit_split_signs():
    csv = b"Transaction Date,Description,Debit,Credit\n2026-10-01,COFFEE,4.50,\n2026-10-02,PAYROLL,,1000\n"
    df = pipeline.clean(pipeline.read_export(csv, "x.csv"))
    assert df["amount"].tolist() == [-4.5, 1000.0]


def test_same_day_identical_rows_kept_but_reupload_deduped():
    csv = b"Date,Description,Amount\n10/01/2026,COFFEE,-4.50\n10/01/2026,COFFEE,-4.50\n"
    a = pipeline.clean(pipeline.read_export(csv, "x.csv"))
    b = pipeline.clean(pipeline.read_export(csv, "x.csv"))
    assert len(a) == 2 and a["fingerprint"].nunique() == 2
    assert set(a["fingerprint"]) == set(b["fingerprint"])


def test_rules_and_anomalies():
    assert rule_category("Whole Foods", "", -50) == "Groceries"
    assert rule_category("Acme Corp Payroll", "", 3850) == "Income"
    rows = [{"date": pd.Timestamp(f"2026-09-{d:02d}").date(), "merchant": "Whole Foods",
             "raw_description": "WHOLEFDS", "amount": -(55.0 + d * 1.37)} for d in range(1, 9)]
    rows.append({"date": pd.Timestamp("2026-09-20").date(), "merchant": "Whole Foods",
                 "raw_description": "WHOLEFDS", "amount": -612.0})
    df, _ = classify(pd.DataFrame(rows), {})
    out = detect_anomalies(df)
    assert out["is_flagged"].tolist() == [False] * 8 + [True]


def test_llm_path_is_used_and_cached(monkeypatch):
    from app import classifier
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    monkeypatch.setattr(classifier, "_call_llm", lambda prompt: (
        'Sure! [{"merchant": "Zork Labs", "category": "Subscriptions", "suspicious": false, "reason": ""},'
        ' {"merchant": "Qwx Pay Ltd", "category": "Other", "suspicious": true, "reason": "unknown foreign processor"}]'))
    df = pd.DataFrame([
        {"date": pd.Timestamp("2026-10-01").date(), "merchant": "Zork Labs", "raw_description": "ZORK", "amount": -20.0},
        {"date": pd.Timestamp("2026-10-02").date(), "merchant": "Qwx Pay Ltd", "raw_description": "QWX", "amount": -89.0},
        {"date": pd.Timestamp("2026-10-03").date(), "merchant": "Netflix", "raw_description": "NETFLIX", "amount": -15.49},
    ])
    out, new = classify(df, {"Netflix": "Subscriptions"})
    assert out["classified_by"].tolist() == ["llm", "llm", "cache"]
    assert new == {"Zork Labs": "Subscriptions", "Qwx Pay Ltd": "Other"}
    flagged = detect_anomalies(out)
    assert flagged.loc[1, "is_flagged"] and "AI:" in flagged.loc[1, "flag_reason"]
