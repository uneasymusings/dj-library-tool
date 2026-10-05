"""Annotations bind exact catalog revisions, never modifying embedded source tags."""

from sqlalchemy import JSON, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from djlib.persistence.models import Base, timestamp


class RecordingAnnotation(Base):
    __tablename__ = "recording_annotations"
    asset_revision_id: Mapped[str] = mapped_column(
        ForeignKey("asset_revisions.id"), primary_key=True
    )
    recording_id: Mapped[str] = mapped_column(ForeignKey("recordings.id"), index=True)
    revision: Mapped[int] = mapped_column(default=1)
    annotations: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String, default=timestamp)
    updated_at: Mapped[str] = mapped_column(String, default=timestamp)
