"""Render real terminal views with fictional fixtures to SVG, HTML and text; no device access.

python scripts/preview_terminal.py --output dist/terminal-previews
"""

import argparse
import io
from pathlib import Path

from rich.console import Console
from rich.terminal_theme import TerminalTheme

from djlib.interfaces import terminal, views

THEME = TerminalTheme(
    (16, 18, 23),
    (226, 229, 235),
    [
        (16, 18, 23),
        (235, 105, 112),
        (126, 204, 153),
        (245, 183, 73),
        (119, 159, 222),
        (182, 147, 220),
        (114, 195, 202),
        (226, 229, 235),
    ],
    [
        (111, 121, 139),
        (246, 133, 140),
        (153, 222, 171),
        (255, 205, 105),
        (149, 184, 239),
        (204, 173, 240),
        (144, 216, 222),
        (255, 255, 255),
    ],
)


def fixtures():
    tracks = [
        {
            "artist": "Velvet Static",
            "title": "Night Bus",
            "version": "Extended Mix",
            "dj": {"bpm": 126, "key": "8A"},
            "properties": {"duration_seconds": 382},
            "format": "flac",
        },
        {
            "artist": "Lumen",
            "title": "Halo",
            "dj": {"bpm": 124, "key": "9A"},
            "properties": {"duration_seconds": 348},
            "format": "wav",
        },
        {
            "artist": "Nia Okoro",
            "title": "Slow Burn",
            "version": "Dub",
            "dj": {},
            "properties": {"duration_seconds": 419},
            "format": "aiff",
        },
    ]
    prep = {
        "track_count": 200,
        "local_files": 184,
        "with_bpm_and_key": 172,
        "import_recorded": True,
        "usb_verified_at_last_check": False,
        "desktop_supported": True,
    }
    crate = {
        "collection_id": "collection_b3dbc1726d094e3cbb85f208a04b50b7",
        "name": "TODO / latest 200",
        "track_count": 200,
        "tracks": tracks,
        "next_cursor": 3,
        "terminal_workflow": prep,
    }
    yield "crate-prep", 80, "crate", crate
    yield "crate-prep-narrow", 40, "crate", crate
    yield (
        "crate-export",
        80,
        "crate",
        {**crate, "terminal_workflow": {**prep, "local_files": 200, "with_bpm_and_key": 200}},
    )
    yield (
        "usb-check",
        80,
        "rekordbox usb",
        {
            "playlist": "Friday",
            "device": "/Volumes/DJ_USB",
            "expected": 200,
            "found": 200,
            "in_order": True,
            "playlist_on_device": True,
            "library_updated": True,
            "export_seconds": 42,
        },
    )
    yield (
        "usb-failed",
        80,
        "rekordbox usb",
        {
            "playlist": "Friday",
            "collection_id": crate["collection_id"],
            "device": "/Volumes/DJ_USB",
            "expected": 200,
            "found": 198,
            "in_order": False,
            "playlist_on_device": True,
            "library_updated": True,
            "missing": ["Lumen - Halo", "Nia Okoro - Slow Burn (Dub)"],
        },
    )
    yield (
        "overview",
        80,
        "status",
        {
            "tracks": 2500,
            "with_bpm": 2400,
            "with_key": 2300,
            "recent_requests": [],
            "recent_collections": [
                {
                    "collection_id": crate["collection_id"],
                    "name": "Friday",
                    "tracks": 200,
                    "in_rekordbox": True,
                }
            ],
            "rekordbox_checked": True,
            "rekordbox_app": True,
        },
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("dist/terminal-previews"))
    output = parser.parse_args().output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name, width, view, result in fixtures():
        stream = io.StringIO()
        term = terminal.Terminal(json=False)
        term.out = Console(
            file=stream,
            record=True,
            width=width,
            force_terminal=True,
            color_system="truecolor",
            theme=terminal.THEME,
            highlight=False,
            _environ={"TERM": "xterm-256color"},
        )
        views.VIEWS[view](term, result)
        term.out.save_svg(
            str(output / f"{name}.svg"),
            title=f"djlib / {name} (illustrative)",
            theme=THEME,
            clear=False,
        )
        term.out.save_html(str(output / f"{name}.html"), theme=THEME, clear=False)
        term.out.save_text(str(output / f"{name}.txt"), clear=False)
    print(output)


if __name__ == "__main__":
    main()
