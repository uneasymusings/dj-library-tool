"""Durable bounded request ledgers, separate from acquisition jobs and catalog assets."""

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from djlib.persistence.models import Base, timestamp


class RequestLedger(Base):
    __tablename__ = "request_ledgers"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(default=1)
    name: Mapped[str]
    request: Mapped[dict] = mapped_column(JSON)
    items: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=timestamp)
    updated_at: Mapped[str] = mapped_column(default=timestamp)


class RequestSubmission(Base):
    __tablename__ = "request_submissions"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    request_hash: Mapped[str]
    ledger_id: Mapped[str] = mapped_column(ForeignKey("request_ledgers.id"))
