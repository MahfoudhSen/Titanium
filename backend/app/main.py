from __future__ import annotations

import datetime as dt
from collections import defaultdict
from contextlib import asynccontextmanager

import pandas as pd
from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import classifier, pipeline
from .db import MerchantCategory, Transaction, Upload, get_db, init_db

@asynccontextmanager
async def lifespan(_app):
    init_db()
    yield


app = FastAPI(title="Titanium API", version="1.0.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class TxnOut(BaseModel):
    id: int
    date: dt.date
    raw_description: str
    merchant: str
    amount: float
    category: str
    is_flagged: bool
    flag_reason: str | None
    classified_by: str

    model_config = {"from_attributes": True}


class TxnPatch(BaseModel):
    category: str | None = None
    is_flagged: bool | None = None
    apply_to_merchant: bool = True


@app.get("/api/health")
def health():
    return {"ok": True, "llm": classifier._llm_backend() or "rules-only"}


@app.get("/api/categories")
def categories():
    return classifier.CATEGORIES


@app.post("/api/upload")
async def upload(files: list[UploadFile] = File(...), db: Session = Depends(get_db)):
    results = []
    for f in files:
        content = await f.read()
        try:
            df = pipeline.clean(pipeline.read_export(content, f.filename or ""))
        except pipeline.PipelineError as e:
            raise HTTPException(422, f"{f.filename}: {e}")
        rows_in = len(df)

        existing = {fp for (fp,) in db.query(Transaction.fingerprint)
                    .filter(Transaction.fingerprint.in_(df["fingerprint"].tolist())).all()}
        df = df[~df["fingerprint"].isin(existing)]

        cache = {m.merchant: m.category for m in db.query(MerchantCategory).all()}
        df, new_cache = classifier.classify(df, cache)
        for merchant, cat in new_cache.items():
            db.merge(MerchantCategory(merchant=merchant, category=cat, source="llm"))

        hist = pd.read_sql(db.query(Transaction.date, Transaction.merchant, Transaction.amount,
                                    Transaction.category).statement, db.bind)
        df = classifier.detect_anomalies(df, hist)

        up = Upload(filename=f.filename, rows_in=rows_in, rows_kept=len(df),
                    duplicates_skipped=rows_in - len(df))
        db.add(up)
        db.flush()
        db.bulk_save_objects([
            Transaction(upload_id=up.id, date=r.date, raw_description=r.raw_description,
                        merchant=r.merchant, amount=float(r.amount), category=r.category,
                        is_flagged=bool(r.is_flagged), flag_reason=r.flag_reason,
                        classified_by="llm" if r.classified_by == "llm" else "rules",
                        fingerprint=r.fingerprint)
            for r in df.itertuples()
        ])
        db.commit()
        results.append({"filename": f.filename, "rows_parsed": rows_in, "rows_added": len(df),
                        "duplicates_skipped": rows_in - len(df),
                        "flagged": int(df["is_flagged"].sum()) if len(df) else 0})
    return {"uploads": results}


def _filtered(db: Session, start, end, category, flagged, q):
    qry = db.query(Transaction)
    if start:
        qry = qry.filter(Transaction.date >= start)
    if end:
        qry = qry.filter(Transaction.date <= end)
    if category:
        qry = qry.filter(Transaction.category == category)
    if flagged is not None:
        qry = qry.filter(Transaction.is_flagged == flagged)
    if q:
        like = f"%{q}%"
        qry = qry.filter(Transaction.merchant.ilike(like) | Transaction.raw_description.ilike(like))
    return qry


@app.get("/api/transactions", response_model=list[TxnOut])
def list_transactions(start: dt.date | None = None, end: dt.date | None = None,
                      category: str | None = None, flagged: bool | None = None,
                      q: str | None = None, limit: int = Query(500, le=5000), offset: int = 0,
                      db: Session = Depends(get_db)):
    return (_filtered(db, start, end, category, flagged, q)
            .order_by(Transaction.date.desc(), Transaction.id.desc()).offset(offset).limit(limit).all())


@app.patch("/api/transactions/{txn_id}", response_model=TxnOut)
def update_transaction(txn_id: int, patch: TxnPatch, db: Session = Depends(get_db)):
    t = db.get(Transaction, txn_id)
    if not t:
        raise HTTPException(404, "Not found")
    if patch.category:
        if patch.category not in classifier.CATEGORIES:
            raise HTTPException(422, "Unknown category")
        t.category, t.classified_by = patch.category, "user"
        if patch.apply_to_merchant:  # learn the correction for this merchant
            db.query(Transaction).filter(Transaction.merchant == t.merchant,
                                         Transaction.classified_by != "user") \
                .update({"category": patch.category, "classified_by": "user"})
            db.merge(MerchantCategory(merchant=t.merchant, category=patch.category, source="user"))
    if patch.is_flagged is not None:
        t.is_flagged = patch.is_flagged
        if not patch.is_flagged:
            t.flag_reason = None
    db.commit()
    db.refresh(t)
    return t


@app.get("/api/summary")
def summary(start: dt.date | None = None, end: dt.date | None = None, db: Session = Depends(get_db)):
    rows = _filtered(db, start, end, None, None, None).all()
    if not rows:
        return {"total_spend": 0, "total_income": 0, "net": 0, "count": 0, "flagged_count": 0,
                "by_category": [], "by_month": [], "top_merchants": [], "date_range": None}

    # refunds (positive amounts in spend categories) net against spending
    spend_cats = lambda t: t.category not in ("Income", "Transfers")
    total_spend = -sum(t.amount for t in rows if spend_cats(t))
    total_income = sum(t.amount for t in rows if t.amount > 0 and t.category == "Income")

    by_cat, by_merchant = defaultdict(float), defaultdict(lambda: [0.0, 0])
    by_month = defaultdict(lambda: {"spend": 0.0, "income": 0.0})
    for t in rows:
        m = t.date.strftime("%Y-%m")
        if spend_cats(t):
            by_cat[t.category] += -t.amount
            by_merchant[t.merchant][0] += -t.amount
            by_merchant[t.merchant][1] += 1
            by_month[m]["spend"] += -t.amount
        elif t.amount > 0 and t.category == "Income":
            by_month[m]["income"] += t.amount

    return {
        "total_spend": round(total_spend, 2),
        "total_income": round(total_income, 2),
        "net": round(total_income - total_spend, 2),
        "count": len(rows),
        "flagged_count": sum(t.is_flagged for t in rows),
        "by_category": sorted([{"category": k, "amount": round(v, 2)} for k, v in by_cat.items()],
                              key=lambda x: -x["amount"]),
        "by_month": [{"month": k, "spend": round(v["spend"], 2), "income": round(v["income"], 2)}
                     for k, v in sorted(by_month.items())],
        "top_merchants": sorted([{"merchant": k, "amount": round(v[0], 2), "count": v[1]}
                                 for k, v in by_merchant.items()], key=lambda x: -x["amount"])[:10],
        "date_range": [min(t.date for t in rows).isoformat(), max(t.date for t in rows).isoformat()],
    }


@app.get("/api/uploads")
def list_uploads(db: Session = Depends(get_db)):
    return [{"id": u.id, "filename": u.filename, "rows_kept": u.rows_kept,
             "duplicates_skipped": u.duplicates_skipped, "created_at": u.created_at.isoformat()}
            for u in db.query(Upload).order_by(Upload.id.desc()).all()]


@app.delete("/api/transactions")
def reset(db: Session = Depends(get_db)):
    n = db.query(Transaction).delete()
    db.query(Upload).delete()
    db.commit()
    return {"deleted": n}
