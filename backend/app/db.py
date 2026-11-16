import os
from datetime import datetime, timezone

from sqlalchemy import (Boolean, Column, Date, DateTime, Float, Integer, String,
                        Text, UniqueConstraint, create_engine)
from sqlalchemy.orm import declarative_base, sessionmaker

# Postgres in Docker / prod; falls back to SQLite for quick local runs.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./titanium.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


class Upload(Base):
    __tablename__ = "uploads"
    id = Column(Integer, primary_key=True)
    filename = Column(String(255), nullable=False)
    rows_in = Column(Integer, default=0)
    rows_kept = Column(Integer, default=0)
    duplicates_skipped = Column(Integer, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None))


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (UniqueConstraint("fingerprint", name="uq_txn_fingerprint"),)

    id = Column(Integer, primary_key=True)
    upload_id = Column(Integer, index=True)
    date = Column(Date, index=True, nullable=False)
    raw_description = Column(Text, nullable=False)
    merchant = Column(String(255), index=True)
    amount = Column(Float, nullable=False)          # negative = money out
    category = Column(String(64), index=True, default="Uncategorized")
    is_flagged = Column(Boolean, default=False, index=True)
    flag_reason = Column(String(255))
    classified_by = Column(String(16), default="rules")  # rules | llm | user
    fingerprint = Column(String(64), nullable=False)


class MerchantCategory(Base):
    """Cache of merchant -> category so the LLM is only asked about new merchants,
    and user corrections stick for future uploads."""
    __tablename__ = "merchant_categories"
    merchant = Column(String(255), primary_key=True)
    category = Column(String(64), nullable=False)
    source = Column(String(16), default="llm")


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
