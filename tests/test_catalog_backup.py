"""Migration snapshots preserve a recoverable SQLite catalog before schema changes."""

import sqlite3

from alembic import command
from alembic.config import Config

from djlib.persistence import database as database_module
from djlib.persistence.database import Database


def test_upgrade_creates_consistent_snapshot_once_and_keeps_history(tmp_path):
    from pathlib import Path

    database = Database(tmp_path / "catalog.db")
    config = Config()
    config.set_main_option(
        "script_location", str(Path(database_module.__file__).parent / "migrations")
    )
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "d3d22d3b4b3d")
        connection.exec_driver_sql(
            "INSERT INTO collections (id,name,revision,created_at) "
            "VALUES ('old','Keep',4,'old-date')"
        )
    database.migrate()
    backups = list((tmp_path / "backups").glob("*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as snapshot:
        assert snapshot.execute("SELECT name,revision FROM collections").fetchall() == [("Keep", 4)]
        assert snapshot.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "d3d22d3b4b3d",
        )
        assert snapshot.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    database.migrate()
    assert list((tmp_path / "backups").glob("*.db")) == backups
    first, second = database.backup(), database.backup()
    assert first["path"] != second["path"]
    assert first["sha256"] == second["sha256"]
    database.engine.dispose()
