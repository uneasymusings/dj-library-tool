"""Remove the empty crates that downloads made before 0.1.0a14, even when no song arrived.

A crate goes only when a download job made it (the job's ``result.collection_id``, under the
job's name), it holds no songs and nothing else names it: other jobs, deliveries, plans or
request lists. Its download jobs then say ``collection_id: null``, as 0.1.0a14 records a
download where every song failed. The catalog is backed up before this runs.
"""

import sqlalchemy as sa
from alembic import op

revision = "a15c20261007"
down_revision = "f12d20261005"
branch_labels = None
depends_on = None

# Tables whose JSON columns may name a crate.
REFERENCES = {
    "jobs": ("request", "result"),
    "deliveries": ("request", "snapshot", "evidence"),
    "plans": ("request",),
    "request_ledgers": ("request", "items"),
}


def upgrade():
    connection = op.get_bind()
    owners: dict[str, list[str]] = {}
    renamed = set()
    for collection_id, name, job_id, job_name in connection.execute(
        sa.text(
            "SELECT c.id, c.name, j.id, json_extract(j.request, '$.name') FROM collections c "
            "JOIN jobs j ON j.kind = 'download' "
            "AND json_extract(j.result, '$.collection_id') = c.id "
            "WHERE NOT EXISTS (SELECT 1 FROM memberships m WHERE m.collection_id = c.id)"
        )
    ):
        owners.setdefault(collection_id, []).append(job_id)
        if name != job_name:
            renamed.add(collection_id)
    for collection_id, jobs in owners.items():
        if collection_id in renamed or named_elsewhere(connection, collection_id, jobs):
            continue
        connection.execute(sa.text("DELETE FROM collections WHERE id = :id"), {"id": collection_id})
        connection.execute(
            sa.text(
                "UPDATE jobs SET result = json_set(result, '$.collection_id', NULL) "
                "WHERE id IN :jobs"
            ).bindparams(sa.bindparam("jobs", expanding=True)),
            {"jobs": jobs},
        )


def named_elsewhere(connection, collection_id: str, jobs: list[str]) -> bool:
    """Whether anything but the crate's own download jobs mentions its ID."""
    for table, columns in REFERENCES.items():
        mentioned = " OR ".join(f"instr({column}, :id) > 0" for column in columns)
        statement = sa.text(
            f"SELECT 1 FROM {table} WHERE ({mentioned})"
            + (" AND id NOT IN :jobs" if table == "jobs" else "")
            + " LIMIT 1"
        )
        params = {"id": collection_id}
        if table == "jobs":
            statement = statement.bindparams(sa.bindparam("jobs", expanding=True))
            params["jobs"] = jobs
        if connection.execute(statement, params).first() is not None:
            return True
    return False


def downgrade():
    # The removed crates were empty; the backup taken before the upgrade still has them.
    pass
