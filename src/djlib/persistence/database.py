"""Single-coordinator catalog access with short, isolated transactions."""

from contextlib import AbstractContextManager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker


class Database:
    def __init__(self, path: Path):
        self.engine = create_engine(f"sqlite:///{path}", connect_args={"timeout": 10})

        @event.listens_for(self.engine, "connect")
        def configure(connection, _record) -> None:
            connection.execute("PRAGMA foreign_keys=ON")
            # A single coordinator does not need WAL; DELETE avoids runtime-specific WAL issues.
            connection.execute("PRAGMA journal_mode=DELETE")

        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self) -> None:
        config = Config()
        config.set_main_option("script_location", str(Path(__file__).parent / "migrations"))
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def transaction(self) -> AbstractContextManager[Session]:
        """Callers use `with db.transaction() as session:` for one short transaction."""
        return self.sessions.begin()
