import os
import pathlib

os.environ["DATABASE_URL"] = "sqlite:///./test_titanium.db"
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("OPENAI_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from app.db import init_db  # noqa: E402
from app.main import app  # noqa: E402

SAMPLES = pathlib.Path(__file__).resolve().parents[2] / "sample_data"


def test_end_to_end():
    init_db()
    with TestClient(app) as c:
        c.delete("/api/transactions")
        files = [("files", (p.name, p.read_bytes(), "text/csv")) for p in sorted(SAMPLES.glob("*.csv"))]
        r = c.post("/api/upload", files=files)
        assert r.status_code == 200, r.text
        ups = r.json()["uploads"]
        assert sum(u["duplicates_skipped"] for u in ups) > 0   # overlapping statements deduped

        again = c.post("/api/upload", files=files).json()["uploads"]
        assert all(u["rows_added"] == 0 for u in again)        # idempotent re-upload

        s = c.get("/api/summary").json()
        assert s["total_spend"] > 0 and s["total_income"] > 0
        assert s["by_category"] and s["by_month"] and s["top_merchants"]

        flagged = c.get("/api/transactions", params={"flagged": True}).json()
        reasons = " | ".join(t["flag_reason"] or "" for t in flagged)
        assert "duplicate" in reasons.lower()
        assert any(t["merchant"] == "Whole Foods" and t["amount"] < -600 for t in flagged)

        # user correction propagates to all rows of that merchant
        t = c.get("/api/transactions", params={"q": "Shell"}).json()[0]
        r = c.patch(f"/api/transactions/{t['id']}", json={"category": "Transport"})
        assert r.status_code == 200
        assert {x["category"] for x in c.get("/api/transactions", params={"q": "Shell"}).json()} == {"Transport"}
