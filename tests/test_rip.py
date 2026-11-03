"""Rip uses the real download worker, then rechecks the same saved set."""

import json
import shutil

from typer.testing import CliRunner

from djlib.interfaces.cli import app
from djlib.sources import web

VIDEO = "https://www.youtube.com/watch?v=abcdefghijk"


def test_rip_previews_without_downloading_and_downloads_only_when_requested(
    library_http, application, audio_factory, monkeypatch
):
    http = library_http
    created = http.post(
        "/requests",
        json={
            "name": "Friday",
            "idempotency_key": "rip-test",
            "items": [{"artist": "Original Tone", "title": "One", "version": "Dub"}],
        },
    ).json()["result"]
    source = audio_factory("download.wav")
    downloads = []

    async def search(provider, query, limit):
        if provider != "youtube":
            return []
        return [
            {
                "url": VIDEO,
                "provider": "youtube",
                "title": "Original Tone - One (Dub)",
                "uploader": "Original Tone - Topic",
                "duration": 200.0,
            }
        ]

    async def download(url, destination):
        downloads.append(url)
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / "tone.wav"
        shutil.copy2(source, target)
        return target, {"kind": "test_download", "source_url": url}

    monkeypatch.setattr(web, "search", search)
    from djlib.jobs import worker

    monkeypatch.setattr(worker, "download", download)
    runner = CliRunner()
    prefix = [
        "--workspace",
        str(application.workspace.root),
        "--json",
        "rip",
        created["request_id"],
    ]
    for args in ([], ["--preview"]):
        reply = runner.invoke(app, prefix + args)
        assert reply.exit_code == 0, reply.output
        result = json.loads(reply.stdout)["result"]
        assert result["preview"] and result["chosen"][0]["source"]["url"] == VIDEO
        assert downloads == []
    reply = runner.invoke(app, prefix + ["--yes"])
    assert reply.exit_code == 0, reply.output
    result = json.loads(reply.stdout)["result"]
    assert result["downloaded"] == 1 and result["job"]["state"] == "completed"
    assert downloads == [VIDEO]
    ledger = http.get(f"/requests/{created['request_id']}").json()["result"]
    assert ledger["items"][0]["state"] == "satisfied"
    assert ledger["items"][0]["input"]["version"] == "Dub"


def test_rip_rejects_non_youtube_and_conflicting_flags(application):
    runner = CliRunner()
    prefix = ["--workspace", str(application.workspace.root), "--json", "rip"]
    for args in (
        ["last", "--preview", "--yes"],
        ["https://example.com/song"],
        [VIDEO, "--preview"],
        ["last", "--title", "Wrong label"],
    ):
        reply = runner.invoke(app, prefix + args)
        assert reply.exit_code != 0
        assert json.loads(reply.stdout)["error"]["code"] == "INPUT_INVALID"


def test_rip_ui_requires_an_explicit_source_and_download_click():
    import subprocess
    from pathlib import Path

    if not shutil.which("node"):
        import pytest

        pytest.skip("needs node")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["node", str(root / "tests/rip_ui.cjs"), str(root / "src/djlib/interfaces/web/app.js")],
        check=True,
        timeout=30,
    )
