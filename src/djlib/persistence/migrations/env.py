"""Alembic environment used by packaged startup and explicit development migrations."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from djlib.persistence.models import Base


def run(connection) -> None:
    context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=context.config.get_main_option("sqlalchemy.url"),
        target_metadata=Base.metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    existing = context.config.attributes.get("connection")
    if existing is not None:
        run(existing)
    else:
        engine = engine_from_config(
            context.config.get_section(context.config.config_ini_section),
            prefix="sqlalchemy.",
            poolclass=pool.NullPool,
        )
        with engine.connect() as connection:
            run(connection)
