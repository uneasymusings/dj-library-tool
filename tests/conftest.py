"""Generated, original audio and isolated catalogs for every test."""

import math
import struct
import wave

import pytest
from fastapi.testclient import TestClient
from mutagen.id3 import TIT2, TPE1
from mutagen.wave import WAVE

from djlib.application.service import Application
from djlib.domain.contracts import CollectionRequest, StartRequest, TrackInput
from djlib.domain.errors import AppError
from djlib.interfaces.client import LocalClient
from djlib.interfaces.service import create_app
from djlib.jobs.worker import Worker
from djlib.persistence.database import Database
from djlib.persistence.models import Job
from djlib.sources import web
from djlib.workspace import Workspace

REAL_PROBE = web.probe


@pytest.fixture(autouse=True)
def isolated_user_config(tmp_path_factory, monkeypatch):
    """Never read or write the developer's real ~/.config/djlib or ~/.claude during tests."""
    monkeypatch.setenv("DJLIB_CONFIG_DIR", str(tmp_path_factory.mktemp("djlib-config")))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path_factory.mktemp("claude-config")))
    monkeypatch.delenv("DJLIB_WORKSPACE", raising=False)
    monkeypatch.delenv("DJLIB_MCP_TOOLS", raising=False)


@pytest.fixture(autouse=True)
def offline_probes(monkeypatch):
    """Availability checks never reach YouTube or SoundCloud: every upload plays unless a test
    replaces ``web.probe`` (tests of the probe itself use ``REAL_PROBE``)."""

    async def probe(url):
        return {"url": url, "duration": None}

    monkeypatch.setattr(web, "probe", probe)


@pytest.fixture
def full_mcp_tools(monkeypatch):
    """Serve every MCP tool (DJLIB_MCP_TOOLS=full) instead of the default core profile."""
    monkeypatch.setenv("DJLIB_MCP_TOOLS", "full")


@pytest.fixture
def audio_factory(tmp_path):
    def create(name="tone.wav", frequency=220, artist="", title="", frames=4410):
        path = tmp_path / "music" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
            samples = [
                int(4000 * math.sin(2 * math.pi * frequency * n / 44100)) for n in range(frames)
            ]
            audio.writeframes(struct.pack("<" + "h" * len(samples), *samples))
        if artist or title:
            audio = WAVE(path)
            audio.add_tags()
            audio.tags.add(TPE1(encoding=3, text=artist))
            audio.tags.add(TIT2(encoding=3, text=title))
            audio.save()
        return path

    return create


@pytest.fixture
def application(tmp_path, audio_factory):
    audio_factory()
    workspace = Workspace(tmp_path / "workspace")
    workspace.initialize([tmp_path / "music"])
    database = Database(workspace.database)
    database.migrate()
    yield Application(workspace, database)
    database.engine.dispose()


def submit_collection(application, paths, *, profile="club", name="Validation", key="collection"):
    tracks = [TrackInput(path=str(path), artist="Test Artist", title=path.stem) for path in paths]
    plan = application.plan(CollectionRequest(name=name, profile=profile, tracks=tracks))
    return application.start(StartRequest(plan_id=plan["plan_id"], revision=1, idempotency_key=key))


async def execute(application, job_id):
    with application.db.transaction() as session:
        job = session.get(Job, job_id)
        job.state = "running"
        job.generation += 1
        generation = job.generation
    await Worker(application).execute(job_id, generation)
    return application.job(job_id)


@pytest.fixture
def library_http(application, monkeypatch):
    app = create_app(application.workspace, "library-transports")
    with TestClient(
        app,
        base_url="http://127.0.0.1",
        headers={"Authorization": "Bearer " + application.workspace.token()},
    ) as http:

        def local_request(self, method, path, *, data=None, params=None):
            response = http.request(method, path, json=data, params=params)
            reply = response.json()
            if not reply["ok"]:
                error = reply["error"]
                raise AppError(
                    error["code"], error["message"], response.status_code, error["retryable"]
                )
            return reply

        # Replace process discovery only; requests reach the real authenticated ASGI app.
        monkeypatch.setattr(LocalClient, "request", local_request)
        yield http
