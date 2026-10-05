"""Persist exact-song request ledgers and revision-bound DJ annotations."""

import sqlalchemy as sa
from alembic import op

revision = "f12d20261005"
down_revision = "e8b7c9d20104"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "request_ledgers",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("items", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_table(
        "request_submissions",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("request_hash", sa.String(), nullable=False),
        sa.Column("ledger_id", sa.String(), sa.ForeignKey("request_ledgers.id"), nullable=False),
    )
    op.create_table(
        "recording_annotations",
        sa.Column(
            "asset_revision_id", sa.String(), sa.ForeignKey("asset_revisions.id"), primary_key=True
        ),
        sa.Column("recording_id", sa.String(), sa.ForeignKey("recordings.id"), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("annotations", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )
    op.create_index(
        "ix_recording_annotations_recording_id", "recording_annotations", ["recording_id"]
    )


def downgrade():
    op.drop_index("ix_recording_annotations_recording_id", table_name="recording_annotations")
    op.drop_table("recording_annotations")
    op.drop_table("request_submissions")
    op.drop_table("request_ledgers")
