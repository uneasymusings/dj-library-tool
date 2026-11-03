"""Record real CLI invocations against an isolated generated-tone library, without sockets.

The HTTP transport is in-process; application matching, jobs, contracts and terminal views
are unchanged. No source audio, online recognition, native app or USB is used.
"""

import argparse
import json
import math
import os
import shlex
import struct
import time
import wave
from contextlib import chdir
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from mutagen.id3 import TIT2, TPE1
from mutagen.wave import WAVE
from typer.testing import CliRunner

from djlib import __version__
from djlib.domain.errors import AppError
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.client import LocalClient
from djlib.interfaces.service import create_app
from djlib.workspace import Workspace

ROOT = Path(__file__).parent


def tone(path, artist, title, frequency):
    samples = [int(2000 * math.sin(2 * math.pi * frequency * n / 44100)) for n in range(176400)]
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 44100, 0, "NONE", "not compressed"))
        audio.writeframes(struct.pack("<" + "h" * len(samples), *samples))
    tags = WAVE(path)
    tags.add_tags()
    tags.tags.add(TPE1(encoding=3, text=artist))
    tags.tags.add(TIT2(encoding=3, text=title))
    tags.save()


def capture(spec, output, work):
    folder = work / spec["id"]
    if folder.exists():
        raise SystemExit(f"Use a fresh work directory: {folder}")
    music = folder / "music"
    music.mkdir(parents=True)
    owned = []
    for index, track in enumerate(spec["tracks"]):
        artist, title, state, *alternative = track
        if state in {"owned", "other"}:
            path = music / f"demo-{index + 1:02}.wav"
            tone(path, artist, alternative[0] if alternative else title, 180 + index * 31)
            owned.append(path)
    tracklist = folder / f"{spec['id']}.txt"
    tracklist.write_text(
        "\n".join(
            f"{index}. {artist} - {title}"
            for index, (artist, title, *_) in enumerate(spec["tracks"], 1)
        )
        + "\n",
        encoding="utf-8",
    )
    workspace = Workspace(folder / "catalog")
    workspace.initialize([music])
    environment = {
        "DJLIB_WORKSPACE": str(workspace.root),
        "DJLIB_CONFIG_DIR": str(folder / "config"),
        "DJLIB_SHAZAM_HOME": str(folder / "no-shazam"),
        "DJLIB_MIXESDB": "off",
        "TERM": "xterm-256color",
        "COLUMNS": "108",
        "LINES": "60",
        "FORCE_COLOR": "1",
        "DJLIB_OUTPUT": "pretty",
    }
    runner = CliRunner()
    stages = []
    with (
        patch.dict(os.environ, environment),
        chdir(folder),
        TestClient(
            create_app(workspace, "showcase"),
            base_url="http://127.0.0.1",
            headers={"Authorization": "Bearer " + workspace.token()},
        ) as http,
    ):
        os.environ.pop("NO_COLOR", None)

        def request(self, method, path, *, data=None, params=None):
            response = http.request(method, path, json=data, params=params)
            body = response.json()
            if not body["ok"]:
                error = body["error"]
                raise AppError(
                    error["code"],
                    error["message"],
                    response.status_code,
                    error["retryable"],
                    error.get("details"),
                )
            return body

        def run(arguments, record=None):
            started = time.monotonic()
            reply = runner.invoke(cli_app, arguments, color=True)
            if reply.exit_code:
                raise RuntimeError(f"{arguments}: {reply.output}\n{reply.exception}")
            if record:
                stages.append(
                    {
                        "label": record,
                        "command": "djlib " + shlex.join(arguments),
                        "stdout": reply.stdout,
                        "stderr": reply.stderr,
                        "seconds": round(time.monotonic() - started, 4),
                        "exit_code": 0,
                    }
                )
            return reply

        with patch.object(LocalClient, "request", request):
            run(["scan"])
            # Saved values are explicitly illustrative operator annotations, never acoustic
            # analysis or claimed measured metadata of these commercial recordings.
            tracks = http.get("/library", params={"limit": 100}).json()["result"]["tracks"]
            for index, track in enumerate(tracks[: max(1, len(tracks) - 2)]):
                body = {
                    "recording_id": track["recording_id"],
                    "asset_revision_id": track["asset_revision_id"],
                    "revision": 0,
                    "idempotency_key": f"demo-values-{index}",
                    "bpm": {"value": 124 + index * 2, "source": "operator", "verified": False},
                    "key": {
                        "value": ["8A", "9A", "10A", "11A"][index % 4],
                        "source": "operator",
                        "verified": False,
                    },
                }
                reply = http.post("/annotations", json=body).json()
                assert reply["ok"], reply
            run(
                ["set", tracklist.name, "--name", spec["name"], "--no-rekordbox"],
                "CHECK THE TRACKLIST",
            )
            run(["requests", "report", "last"], "FIND THE MISSING MIXES")
            run(["crate", "last"], "PREP YOUR CRATE")
            summary = http.get("/requests", params={"limit": 1}).json()["result"]["requests"][0]
            collection = http.get("/collections", params={"limit": 1}).json()["result"][
                "collections"
            ][0]
            prep = http.get(f"/collections/{collection['collection_id']}/workflow").json()["result"]
            assert prep["local_files"] == prep["track_count"] > 0
            assert not prep["import_recorded"] and not prep["usb_verified_at_last_check"]
            assert prep["track_count"] == sum(t[2] == "owned" for t in spec["tracks"])
            assert summary["counts"]["ids"] == sum(t[2] == "id" for t in spec["tracks"])
    # Movies replay the captured stdout; the raw capture stays outside the repository.
    result = {
        **spec,
        "version": __version__,
        "columns": 108,
        "stages": stages,
        "counts": summary.get("counts"),
        "prep": prep,
        "disclosure": (
            "Replay of actual CLI output / demo library / illustrative saved BPM-key / "
            "no live recognition or USB export"
        ),
    }
    (output / f"{spec['id']}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"{spec['id']}: {len(stages)} successful CLI commands, "
        f"{prep['track_count']} tracks in crate"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--only")
    arguments = parser.parse_args()
    output, work = arguments.output.resolve(), arguments.work.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for spec in json.loads((ROOT / "sets.json").read_text(encoding="utf-8")):
        if arguments.only and spec["id"] != arguments.only:
            continue
        capture(spec, output, work)


if __name__ == "__main__":
    main()
