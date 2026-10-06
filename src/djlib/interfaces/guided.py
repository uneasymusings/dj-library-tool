"""Interactive terminal flows that build the same validated inputs as JSON files.

Every answer comes from the person at the keyboard; counts and recording IDs are filled in
only from the frozen delivery manifest after they confirm they checked those tracks.
"""

import getpass
import json
import sys
from pathlib import Path

from rich.padding import Padding
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table
from rich.text import Text

from djlib.domain.contracts import DeliveryObservation
from djlib.domain.errors import AppError
from djlib.interfaces.terminal import Terminal, plural
from djlib.interfaces.views import STAGE_LABELS, WORKFLOW_LABELS, header, track_label

GUIDED_STAGES = ("imported", "analyzed", "native_exported", "device_library_checked")

QUESTIONS = {
    "imported": "Do all {count} appear in {app}, in the playlists above?",
    "analyzed": "Did {app} finish analyzing all {count} (BPM, grid and key)?",
    "native_exported": "Did {app} finish exporting all {count} to the USB?",
    "device_library_checked": "Do all playlists and {count} show in the USB's device library?",
}


def observe(term: Terminal, client, delivery_id: str, stage: str | None) -> DeliveryObservation:
    """Ask what was actually seen in the app and return a validated observation."""
    if not sys.stdin.isatty():
        raise AppError(
            "INPUT_INVALID",
            "Pass --file with an observation JSON; interactive prompts need a terminal.",
        )
    delivery = client.request("GET", f"/deliveries/{delivery_id}")["result"]
    request = delivery.get("request") or {}
    workflow = request.get("workflow", "")
    app = "Serato" if "serato" in workflow else "rekordbox"
    stage = stage or delivery.get("next_step")
    if stage == "hardware_playback":
        raise AppError(
            "INPUT_INVALID",
            "Record hardware playback with --file; it needs player, firmware and USB details.",
        )
    if stage not in GUIDED_STAGES:
        raise AppError(
            "STAGE_REQUIRED",
            f"Nothing to observe yet: the next step is {str(stage).replace('_', ' ')}. "
            f"Run 'djlib delivery get {delivery_id}' for details.",
        )
    manifest = _manifest(delivery)
    tracks = manifest.get("tracks") or []
    count = plural(int(manifest.get("unique_track_count") or len(tracks)), "track")

    header(
        term,
        f"Record “{STAGE_LABELS[stage].lower()}”",
        f"{request.get('name', '')} · {WORKFLOW_LABELS.get(workflow, workflow)}",
    )
    term.out.print()
    playlists = manifest.get("playlists") or []
    if playlists:
        term.out.print(Text("Playlists", style="heading"))
        for playlist in playlists:
            term.out.print(
                Text.assemble(
                    "  ",
                    (playlist.get("name") or "", "heading"),
                    (f"  {plural(int(playlist.get('track_count') or 0), 'track')}", "muted"),
                )
            )
        term.out.print()
    term.out.print(Text("Tracks to check", style="heading"))
    grid = Table.grid(padding=(0, 2))
    grid.add_column(justify="right", style="muted")
    grid.add_column(overflow="fold")
    for number, track in enumerate(tracks, 1):
        grid.add_row(str(number), track_label(track))
    term.out.print(Padding(grid, (0, 0, 0, 2)))
    term.out.print()

    passed = Confirm.ask(
        Text(QUESTIONS[stage].format(count=count, app=app), style="heading"),
        console=term.out,
    )
    if passed:
        track_count = int(manifest["unique_track_count"])
        playlist_counts = dict(manifest["playlist_counts"])
        checked = [track["recording_id"] for track in tracks]
    else:
        track_count = IntPrompt.ask(
            f"How many of the {count} did you see?", console=term.out, default=0
        )
        playlist_counts = {}
        for playlist in playlists:
            playlist_counts[playlist["collection_id"]] = (
                track_count
                if len(playlists) == 1
                # A Text prompt: playlist names are user data, never Rich markup.
                else IntPrompt.ask(
                    Text(f"  Tracks in “{playlist.get('name')}”"), console=term.out, default=0
                )
            )
        checked = []
    app_version = Prompt.ask(
        f"{app} version shown in About",
        console=term.out,
        default=str(request.get("app_version") or ""),
    )
    observer = Prompt.ask("Your name", console=term.out, default=_user())
    default_notes = (
        f"Checked all {count} in the {app} UI."
        if passed
        else f"Saw {track_count} of {count} in the {app} UI."
    )
    notes = Prompt.ask("Notes", console=term.out, default=default_notes)
    outcome = "passed" if passed else "failed"
    term.out.print()
    if not Confirm.ask(
        Text.assemble(
            "Record ",
            (STAGE_LABELS[stage].lower(), "heading"),
            ": ",
            (outcome, "ok" if passed else "bad"),
            "?",
        ),
        console=term.out,
        default=True,
    ):
        raise AppError("INPUT_INVALID", "Nothing was recorded.")
    return DeliveryObservation(
        revision=int(delivery["revision"]),
        stage=stage,
        app_version=app_version,
        track_count=track_count,
        playlist_counts=playlist_counts,
        checked_recording_ids=checked,
        observer=observer,
        notes=notes,
        method="native_app_ui",
        outcome=outcome,
    )


def _manifest(delivery: dict) -> dict:
    job = delivery.get("preparation_job") or {}
    path = (job.get("result") or {}).get("manifest_path")
    if job.get("state") != "completed" or not path:
        raise AppError("PREPARATION_REQUIRED", "Prepare the delivery before native app work.")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AppError("MANIFEST_CHANGED", "The prepared delivery manifest is unreadable.") from exc


def _user() -> str:
    try:
        return getpass.getuser()
    except (KeyError, OSError):
        return ""
