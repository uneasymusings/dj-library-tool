"""Crate prep reads whole-crate evidence and keeps useful output at terminal widths."""

import io

import pytest
from rich.cells import cell_len
from rich.console import Console

from djlib.domain.errors import AppError
from djlib.interfaces import terminal, views
from djlib.interfaces.envelope import envelope

COLLECTION = "collection_b3dbc1726d094e3cbb85f208a04b50b7"


def workflow(**changes):
    return {
        "track_count": 200,
        "local_files": 184,
        "with_bpm_and_key": 172,
        "import_recorded": True,
        "usb_verified_at_last_check": False,
        "desktop_supported": True,
        **changes,
    }


def render(width=80, **changes):
    stream = io.StringIO()
    term = terminal.Terminal(json=False)
    term.out = Console(file=stream, width=width, theme=terminal.THEME, highlight=False)
    views.collection(
        term,
        {
            "name": "TODO / latest 200",
            "collection_id": COLLECTION,
            "track_count": 200,
            "tracks": [],
            "terminal_workflow": workflow(**changes),
        },
    )
    return stream.getvalue()


@pytest.mark.parametrize("width", [32, 40, 60, 80, 120])
def test_prep_widths_show_counts_and_blockers_without_fake_readiness(width):
    output = render(width)
    assert "djlib / crate prep" in output
    assert "184 / 200" in output and "172 / 200" in output
    assert "16 files" in output and "28 tracks" in output
    assert "ready" not in output.lower()
    assert "djlib scan" in output and "rekordbox usb" not in output
    assert max(map(cell_len, output.splitlines())) <= width


def test_plain_ascii_keeps_the_same_information(monkeypatch):
    monkeypatch.setattr(terminal.Terminal, "unicode", property(lambda self: False))
    output = render()
    assert output.isascii()
    assert "[##################..]" in output
    assert "184 / 200" in output and "Needs attention" in output


def test_narrow_tracks_keep_full_mix_names_and_bpm():
    stream = io.StringIO()
    term = terminal.Terminal(json=False)
    term.out = Console(file=stream, width=40, theme=terminal.THEME)
    term.out.print(
        views.track_table(
            term,
            [
                {
                    "artist": "Velvet Static",
                    "title": "Night Bus",
                    "version": "Extended Mix",
                    "dj": {"bpm": 126, "key": "8A"},
                    "properties": {"duration_seconds": 382},
                }
            ],
            numbered=True,
        )
    )
    output = stream.getvalue()
    assert "Velvet Static - Night Bus" in output
    assert "Extended Mix" in " ".join(output.split())
    assert "126 BPM / 8A / 6:22" in output and "…" not in output
    assert max(map(cell_len, output.splitlines())) <= 40


@pytest.mark.parametrize(
    ("changes", "command"),
    [
        ({"local_files": 200, "import_recorded": False}, "rekordbox push b3dbc172"),
        ({"local_files": 200}, "rekordbox pull"),
        ({"local_files": 200, "with_bpm_and_key": 200}, "rekordbox usb b3dbc172"),
        ({"desktop_supported": False, "local_files": 200}, "djlib ui"),
        ({"action": {"state": "running", "action": "usb"}}, "djlib ui"),
    ],
)
def test_next_action_follows_the_first_unfinished_stage(changes, command):
    output = render(**changes)
    steps = output.split("Next\n")[1]
    assert command in steps
    assert steps.count("djlib ") == 1


def test_last_usb_check_is_historical_and_empty_crates_never_look_complete():
    output = render(
        local_files=200,
        with_bpm_and_key=200,
        usb_verified_at_last_check=True,
        usb_device="/Volumes/DJ_USB",
    )
    assert "files and order checked on DJ_USB" in output
    assert "hardware playback has not been tested" in output
    assert "Test this USB on your player" in output
    empty = render(track_count=0, local_files=0, with_bpm_and_key=0, import_recorded=False)
    assert "0 / 0" in empty and "█" not in empty
    assert "rekordbox push" not in empty and "rekordbox usb" not in empty


def test_old_or_failed_action_is_visible_and_never_looks_like_a_usb_pass():
    output = render(
        previous_action_is_stale=True,
        action={"state": "failed", "action": "usb", "error": {"message": "USB disconnected"}},
    )
    assert "previous import/export result no longer applies" in output
    assert "USB disconnected" in output
    assert "files and order checked" not in output


def test_human_summary_uses_whole_crate_route_without_mutating_response():
    stream = io.StringIO()
    term = terminal.Terminal(json=False)
    term.out = Console(file=stream, width=80, theme=terminal.THEME)
    response = envelope(
        {"collection_id": COLLECTION, "name": "TODO", "track_count": 200, "tracks": []}
    )
    requested = []

    class Client:
        def request(self, method, path):
            requested.append((method, path))
            return envelope(workflow())

    views.show(term, "crate", response, client_factory=Client)
    assert requested == [("GET", f"/collections/{COLLECTION}/workflow")]
    assert "184 / 200" in stream.getvalue()
    assert "terminal_workflow" not in response["result"]

    class OlderService:
        def request(self, method, path):
            raise AppError("NOT_FOUND", "No workflow route")

    views.show(term, "crate", response, client_factory=OlderService)
    assert "Prep status unavailable" in stream.getvalue()


@pytest.mark.parametrize("width", [40, 60, 80, 120])
def test_live_progress_keeps_the_operation_and_counts(width):
    stream = io.StringIO()
    term = terminal.Terminal(json=False)
    term.err = Console(file=stream, width=width, theme=terminal.THEME)
    progress = terminal.progress_bar(term)
    task = progress.add_task("Reading your music", total=2000, detail="Ctrl-C: background")
    progress.update(task, completed=1000)
    term.err.print(progress.get_renderable())
    output = stream.getvalue()
    assert "Reading your music" in output and "1,000/2,000" in output
    assert max(map(cell_len, output.splitlines())) <= width
