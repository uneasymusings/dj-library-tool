"""Single-coordinator catalog access with short, isolated transactions."""

import hashlib
import os
import sqlite3
import tempfile
import time
from contextlib import AbstractContextManager, closing
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from djlib.domain.errors import AppError


class Database:
    def __init__(self, path: Path):
        self.path = Path(path).absolute()
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
        if self.path.exists() and self.path.stat().st_size:
            with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as source:
                table = source.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='alembic_version'"
                ).fetchone()
                current = (
                    source.execute("SELECT version_num FROM alembic_version").fetchall()
                    if table
                    else []
                )
            if current != [(ScriptDirectory.from_config(config).get_current_head(),)]:
                self.backup(reason="before_migration")
        with self.engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")

    def backup(self, *, reason="manual") -> dict:
        """Create a consistent, checked catalog snapshot without overwriting any prior backup."""
        if not self.path.is_file() or not self.path.stat().st_size:
            raise AppError(
                "CATALOG_REQUIRED", "Start the workspace once before backing up its catalog."
            )
        directory = self.path.parent / "backups"
        directory.mkdir(mode=0o700, exist_ok=True)
        target = directory / f"catalog-{uuid4().hex}.db"
        descriptor, temporary = tempfile.mkstemp(prefix=".catalog-", suffix=".db", dir=directory)
        os.close(descriptor)
        started = time.monotonic()

        def progress(_status, _remaining, _total):
            if time.monotonic() - started > 30:
                raise AppError(
                    "BACKUP_TIMEOUT", "Catalog backup exceeded 30 seconds; no migration ran."
                )

        try:
            with (
                closing(
                    sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
                ) as source,
                closing(sqlite3.connect(temporary)) as snapshot,
            ):
                source.backup(snapshot, pages=256, progress=progress, sleep=0.05)
                if snapshot.execute("PRAGMA integrity_check").fetchone() != ("ok",):
                    raise AppError("BACKUP_INVALID", "Catalog snapshot failed its integrity check.")
            with open(temporary, "r+b") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            return {
                "path": str(target),
                "sha256": digest,
                "reason": reason,
                "scope": "catalog_only; music and native DJ databases are not included",
            }
        except sqlite3.Error as exc:
            raise AppError("BACKUP_FAILED", "Catalog backup failed; no migration ran.") from exc
        finally:
            Path(temporary).unlink(missing_ok=True)

    def transaction(self) -> AbstractContextManager[Session]:
        """Callers use `with db.transaction() as session:` for one short transaction."""
        return self.sessions.begin()
