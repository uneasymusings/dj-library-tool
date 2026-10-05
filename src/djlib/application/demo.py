"""A credential-free example that creates original tones, never copyrighted music."""

import math
import struct
import wave

from mutagen.id3 import TIT2, TPE1
from mutagen.wave import WAVE

from djlib.domain.contracts import CollectionRequest, TrackInput
from djlib.domain.errors import AppError
from djlib.interfaces.client import LocalClient
from djlib.workspace import Workspace


def run_demo(workspace: Workspace) -> dict:
    source = workspace.root / "demo-source"
    if workspace.config_path.exists() and not source.is_dir():
        # Never mix generated tones into a real library.
        raise AppError(
            "DEMO_WORKSPACE_IN_USE",
            "This workspace already holds a library. Run the demo in a new folder, "
            "for example: djlib --workspace ./djlib-demo demo",
            409,
        )
    workspace.initialize()
    source.mkdir(exist_ok=True)
    tracks = []
    for index, frequency in enumerate((220, 330, 440), 1):
        path = source / f"tone-{index}.wav"
        if not path.exists():
            samples = [
                int(4000 * math.sin(2 * math.pi * frequency * n / 44100)) for n in range(44100)
            ]
            with wave.open(str(path), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(44100)
                audio.writeframes(struct.pack("<" + "h" * len(samples), *samples))
            tagged = WAVE(path)
            tagged.add_tags()
            tagged.tags.add(TPE1(encoding=3, text="DJLIB synthetic demo"))
            tagged.tags.add(TIT2(encoding=3, text=f"Tone {index}"))
            tagged.save()
        tracks.append(
            TrackInput(path=str(path), artist="DJLIB synthetic demo", title=f"Tone {index}")
        )
    client = LocalClient(workspace)
    plan = client.request(
        "POST",
        "/plans",
        data=CollectionRequest(name="Synthetic demo", tracks=tracks).model_dump(mode="json"),
    )["result"]
    job = client.request(
        "POST",
        "/jobs",
        data={
            "plan_id": plan["plan_id"],
            "revision": 1,
            "idempotency_key": f"demo:{plan['plan_id']}",
        },
    )["result"]
    finished = client.wait(job["job_id"], 30)["result"]
    if finished["state"] != "completed" or finished["outcome"] != "complete":
        raise AppError("DEMO_INCOMPLETE", "Demo ingestion is incomplete; inspect jobs and reviews.")
    export = client.request(
        "POST",
        "/exports",
        data={
            "collection_id": finished["result"]["collection_id"],
            "idempotency_key": f"demo-export:{job['job_id']}",
        },
    )["result"]
    return {
        "ingestion": finished,
        "export": client.wait(export["job_id"], 30)["result"],
        "source": "three generated one-second sine waves",
        "workspace": str(workspace.root),
    }
