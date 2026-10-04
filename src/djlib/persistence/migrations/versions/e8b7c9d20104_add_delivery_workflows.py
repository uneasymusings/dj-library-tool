"""Persist target-specific delivery snapshots and observations."""

import sqlalchemy as sa
from alembic import op

revision = "e8b7c9d20104"
down_revision = "d3d22d3b4b3d"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "deliveries",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("job_id", sa.String(), sa.ForeignKey("jobs.id"), nullable=True),
        sa.Column("created_at", sa.String(), nullable=False),
        sa.Column("updated_at", sa.String(), nullable=False),
    )


def downgrade():
    op.drop_table("deliveries")
