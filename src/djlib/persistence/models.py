"""Catalog tables; byte revisions, logical assets, and recording identity stay separate."""

from datetime import UTC, datetime

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class Base(DeclarativeBase):
    pass


class Plan(Base):
    __tablename__ = "plans"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(default=1)
    request: Mapped[dict] = mapped_column(JSON)
    profile: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=timestamp)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str]
    state: Mapped[str] = mapped_column(index=True, default="queued")
    outcome: Mapped[str | None]
    generation: Mapped[int] = mapped_column(default=0)
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(default=timestamp)
    updated_at: Mapped[str] = mapped_column(default=timestamp)


class JobItem(Base):
    __tablename__ = "job_items"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    position: Mapped[int]
    state: Mapped[str] = mapped_column(default="pending")
    request: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON, default=dict)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"), index=True)
    kind: Mapped[str]
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[str] = mapped_column(default=timestamp)


class Submission(Base):
    __tablename__ = "submissions"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    request_hash: Mapped[str]
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id"))


class Recording(Base):
    __tablename__ = "recordings"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    identity_key: Mapped[str] = mapped_column(unique=True, index=True)
    artist: Mapped[str]
    title: Mapped[str]
    version: Mapped[str] = mapped_column(default="")
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)


class Asset(Base):
    __tablename__ = "assets"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), index=True)
    provenance: Mapped[dict] = mapped_column(JSON)


class AssetRevision(Base):
    __tablename__ = "asset_revisions"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    asset_id: Mapped[str] = mapped_column(ForeignKey("assets.id"), index=True)
    sha256: Mapped[str] = mapped_column(unique=True, index=True)
    properties: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(default=timestamp)


class FileLocation(Base):
    __tablename__ = "file_locations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    revision_id: Mapped[str] = mapped_column(ForeignKey("asset_revisions.id"), index=True)
    path: Mapped[str] = mapped_column(unique=True)
    managed: Mapped[bool] = mapped_column(default=False)


class Collection(Base):
    __tablename__ = "collections"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str]
    revision: Mapped[int] = mapped_column(default=1)
    created_at: Mapped[str] = mapped_column(default=timestamp)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("collection_id", "recording_id"),)
    id: Mapped[str] = mapped_column(String, primary_key=True)
    collection_id: Mapped[str] = mapped_column(ForeignKey("collections.id"), index=True)
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"))
    revision_id: Mapped[str] = mapped_column(ForeignKey("asset_revisions.id"))
    position: Mapped[int]


class Operation(Base):
    __tablename__ = "operations"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("job_items.id"), index=True)
    phase: Mapped[str]
    detail: Mapped[dict] = mapped_column(JSON)


class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("job_items.id"), index=True)
    revision: Mapped[int] = mapped_column(default=1)
    state: Mapped[str] = mapped_column(default="open")
    reason: Mapped[str]
    evidence: Mapped[dict] = mapped_column(JSON)


class Delivery(Base):
    __tablename__ = "deliveries"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(default=1)
    request: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[dict] = mapped_column(JSON)
    evidence: Mapped[dict] = mapped_column(JSON, default=dict)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id"))
    created_at: Mapped[str] = mapped_column(default=timestamp)
    updated_at: Mapped[str] = mapped_column(default=timestamp)
