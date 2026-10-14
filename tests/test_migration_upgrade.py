"""Upgrade the public alpha schema without rewriting catalog bytes, identities, or membership."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, inspect, select

from djlib.application.delivery import delivery_status
from djlib.application.organization import annotate_recording, annotation_status
from djlib.application.requests import create_request, get_request
from djlib.application.service import Application
from djlib.audio.inspection import checksum, inspect_audio
from djlib.domain.contracts import DeliveryRequest, recording_key
from djlib.domain.organization_contracts import AnnotationRequest
from djlib.domain.request_contracts import RequestCreate, RequestItem
from djlib.persistence import database as database_module
from djlib.persistence.database import Database
from djlib.persistence.models import (
    Asset,
    AssetRevision,
    Collection,
    Delivery,
    FileLocation,
    Membership,
    Recording,
)
from djlib.workspace import Workspace

PUBLIC_A2_REVISION = "d3d22d3b4b3d"
DELIVERY_REVISION = "e8b7c9d20104"
REQUEST_REVISION = "f12d20261005"
DOWNLOAD_CRATES_REVISION = "a15c20261007"


def upgrade_to(database, revision):
    config = Config()
    config.set_main_option(
        "script_location", str(Path(database_module.__file__).parent / "migrations")
    )
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, revision)


def snapshot(database, table_names):
    values = {}
    with database.engine.connect() as connection:
        for name in sorted(table_names):
            table = Table(name, MetaData(), autoload_with=connection)
            rows = connection.execute(select(table).order_by(*table.primary_key.columns))
            values[name] = [dict(row) for row in rows.mappings()]
    return values


def schema_revision(database):
    with database.engine.connect() as connection:
        return connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()


def test_public_a2_catalog_survives_delivery_and_request_migrations(tmp_path, audio_factory):
    sources = [audio_factory(f"legacy-{n}.wav", frequency=220 + 110 * n) for n in range(2)]
    workspace = Workspace(tmp_path / "upgrade-workspace")
    workspace.initialize([sources[0].parent])
    database = Database(workspace.database)
    try:
        upgrade_to(database, PUBLIC_A2_REVISION)
        assert schema_revision(database) == PUBLIC_A2_REVISION
        original_tables = set(inspect(database.engine).get_table_names()) - {"alembic_version"}
        assert not original_tables.intersection(
            {"deliveries", "request_ledgers", "request_submissions", "recording_annotations"}
        )
        # Seed the actual a2 schema, without using current metadata.create_all.
        with database.transaction() as session:
            session.add(Collection(id="legacy-set", name="Pré-existing warm-up", revision=7))
            for number, source in enumerate(sources):
                measured = inspect_audio(source)
                labels = {
                    "artist": "Original migration artist",
                    "title": f"Tone {number}",
                    "version": "Original",
                }
                session.add(
                    Recording(
                        id=f"legacy-rec-{number}",
                        identity_key=recording_key(**labels),
                        **labels,
                        evidence={
                            "method": "user_supplied",
                            "notes": "Preserve α2 evidence",
                            "acoustic_identity_verified": False,
                        },
                    )
                )
                session.flush()
                session.add(
                    Asset(
                        id=f"legacy-asset-{number}",
                        recording_id=f"legacy-rec-{number}",
                        provenance={"kind": "user_supplied", "source": str(source)},
                    )
                )
                session.flush()
                session.add(
                    AssetRevision(
                        id=f"legacy-rev-{number}",
                        asset_id=f"legacy-asset-{number}",
                        sha256=measured.sha256,
                        properties=measured.as_dict(),
                    )
                )
                session.flush()
                session.add(
                    FileLocation(
                        id=f"legacy-location-{number}",
                        revision_id=f"legacy-rev-{number}",
                        path=str(source),
                        managed=False,
                    )
                )
                session.add(
                    Membership(
                        id=f"legacy-member-{number}",
                        collection_id="legacy-set",
                        recording_id=f"legacy-rec-{number}",
                        revision_id=f"legacy-rev-{number}",
                        position=1 - number,
                    )
                )
        original_data = snapshot(database, original_tables)
        original_hashes = {source: checksum(source) for source in sources}
        application = Application(workspace, database)
        collection = application.collection("legacy-set")
        assert collection["revision"] == 7
        assert [track["recording_id"] for track in collection["tracks"]] == [
            "legacy-rec-1",
            "legacy-rec-0",
        ]

        upgrade_to(database, DELIVERY_REVISION)
        assert schema_revision(database) == DELIVERY_REVISION
        assert snapshot(database, original_tables) == original_data
        assert "deliveries" in inspect(database.engine).get_table_names()
        assert "request_ledgers" not in inspect(database.engine).get_table_names()
        # Seed an intermediate delivery using that migration's fields. The current
        # application correctly expects all migrations before serving use cases.
        pilot = {
            "delivery_id": "legacy-delivery",
            "snapshot": {"collections": [collection], "tracks": collection["tracks"]},
        }
        with database.transaction() as session:
            session.add(
                Delivery(
                    id=pilot["delivery_id"],
                    snapshot=pilot["snapshot"],
                    request=DeliveryRequest(
                        name="Pending pre-upgrade pilot",
                        collection_ids=["legacy-set"],
                        workflow="rekordbox_import",
                        app_version="synthetic-test-version",
                    ).model_dump(mode="json"),
                )
            )
        delivery_data = snapshot(database, {"deliveries"})

        # Use the same startup migration entry point as the packaged local coordinator.
        database.migrate()
        assert schema_revision(database) == DOWNLOAD_CRATES_REVISION
        assert set(inspect(database.engine).get_table_names()) >= {
            "deliveries",
            "request_ledgers",
            "request_submissions",
            "recording_annotations",
        }
        assert snapshot(database, original_tables) == original_data
        assert snapshot(database, {"deliveries"}) == delivery_data
        assert application.collection("legacy-set") == collection
        assert delivery_status(application, pilot["delivery_id"])["snapshot"] == pilot["snapshot"]

        # New functionality can reference the old IDs directly without re-ingesting source bytes.
        ledger = create_request(
            application,
            RequestCreate(
                name="Old-catalog lookup after upgrade",
                idempotency_key="upgrade-request",
                items=[
                    RequestItem(
                        artist="Original migration artist", title="Tone 1", version="Original"
                    )
                ],
            ),
        )
        assert ledger["items"][0]["accepted"]["recording_id"] == "legacy-rec-1"
        annotation = annotate_recording(
            application,
            AnnotationRequest(
                recording_id="legacy-rec-1",
                asset_revision_id="legacy-rev-1",
                revision=0,
                idempotency_key="upgrade-annotation",
                notes="Post-upgrade catalog note; source untouched",
            ),
        )
        assert annotation["outcome"] == "complete"
        assert annotation_status(application, "legacy-rec-1", "legacy-rev-1")["revision"] == 1
        post_upgrade_tables = set(inspect(database.engine).get_table_names()) - {"alembic_version"}
        upgraded_data = snapshot(database, post_upgrade_tables)
        database.migrate()
        assert snapshot(database, post_upgrade_tables) == upgraded_data
        assert get_request(application, ledger["request_id"]) == ledger
        assert application.collection("legacy-set") == collection
        assert {source: checksum(source) for source in sources} == original_hashes
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
            assert connection.exec_driver_sql("PRAGMA integrity_check").scalar_one() == "ok"
    finally:
        database.engine.dispose()


def test_empty_crates_of_failed_downloads_are_removed_once(tmp_path):
    """Before 0.1.0a14 every download made its crate up front, also when nothing arrived."""
    import sqlite3
    from contextlib import closing

    from djlib.application.catalog_paging import summary
    from djlib.jobs.worker import Worker
    from djlib.persistence.models import Job

    workspace = Workspace(tmp_path / "crates-workspace")
    (tmp_path / "music").mkdir()
    workspace.initialize([tmp_path / "music"])
    database = Database(workspace.database)
    gone = {"code": "SOURCE_UNAVAILABLE", "message": "Gone.", "retryable": False}
    try:
        upgrade_to(database, REQUEST_REVISION)
        with database.transaction() as session:

            def crate(collection_id, name, kind="download", job_result=None):
                session.add(Collection(id=collection_id, name=name, revision=1))
                if kind:
                    session.add(
                        Job(
                            id=f"job-{collection_id}",
                            kind=kind,
                            state="completed",
                            outcome="completed_with_gaps",
                            request={"name": name, "tracks": []},
                            result={"collection_id": collection_id, **(job_result or {})},
                        )
                    )

            # An old failed download's crate goes; its job keeps the rest of its result.
            crate("failed", "Tonight — downloads", job_result={"error": gone})
            # A download crate with a song stays.
            crate("fetched", "Friday — downloads")
            session.add(Recording(id="rec", identity_key="a|b|", artist="A", title="B"))
            session.flush()
            session.add(Asset(id="asset", recording_id="rec", provenance={}))
            session.flush()
            session.add(AssetRevision(id="rev", asset_id="asset", sha256="0" * 64, properties={}))
            session.flush()
            session.add(
                Membership(
                    id="member",
                    collection_id="fetched",
                    recording_id="rec",
                    revision_id="rev",
                    position=0,
                )
            )
            # Crates you made yourself stay, whatever their name.
            crate("mine", "Mine — downloads", kind="collection")
            crate("loose", "Loose — downloads", kind=None)
            # An empty download crate that a delivery names stays.
            crate("delivered", "Delivered — downloads")
            session.add(
                Delivery(id="delivery", request={"collection_ids": ["delivered"]}, snapshot={})
            )
        tables = set(inspect(database.engine).get_table_names()) - {"alembic_version"}
        before = snapshot(database, tables)

        database.migrate()
        assert schema_revision(database) == DOWNLOAD_CRATES_REVISION
        after = snapshot(database, tables)
        assert after["collections"] == [r for r in before["collections"] if r["id"] != "failed"]
        jobs = {row["id"]: row for row in after["jobs"]}
        assert jobs["job-failed"]["result"] == {"collection_id": None, "error": gone}
        assert [r for r in after["jobs"] if r["id"] != "job-failed"] == [
            r for r in before["jobs"] if r["id"] != "job-failed"
        ]
        unchanged = tables - {"collections", "jobs"}
        assert {t: after[t] for t in unchanged} == {t: before[t] for t in unchanged}
        [backup] = (workspace.database.parent / "backups").glob("*.db")
        with closing(sqlite3.connect(backup)) as saved:
            assert ("failed",) in saved.execute("SELECT id FROM collections").fetchall()

        # Neither `status` nor `crates` lists it.
        application = Application(workspace, database)
        names = {
            "Friday — downloads",
            "Mine — downloads",
            "Loose — downloads",
            "Delivered — downloads",
        }
        listed = summary(application)["recent_collections"]
        assert {row["name"] for row in listed} == names
        assert {row["name"] for row in application.saved("collections")["collections"]} == names

        # Once only, and a retried download makes its crate again when a song arrives.
        database.migrate()
        assert snapshot(database, tables) == after
        with database.transaction() as session:
            job = session.get(Job, "job-failed")
            collection_id = Worker.download_collection(session, job)
            assert collection_id != "failed" and session.get(Collection, collection_id)
        with database.engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        database.engine.dispose()
