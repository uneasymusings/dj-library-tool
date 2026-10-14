"""What a DJ sees after `djlib set` and `djlib identify`: the whole set in set order,
owned/missing/other mix/ID, store searches and times that play the set from that moment."""

import io
import json
import re

import pytest
from mcp import Client
from rich.console import Console
from typer.testing import CliRunner

from djlib.application import identification
from djlib.domain.errors import AppError
from djlib.interfaces import identify_cli, mcp_server, set_sources, terminal, views
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.rekordbox_cli import set_track
from djlib.native import rekordbox_mac
from tests.test_headline_workflow import build
from tests.test_identification import URL
from tests.test_identify_cli import LISTED, fakes
from tests.test_identify_cli import invoke as identify_invoke
from tests.test_terminal import COLLECTION, REQUEST, SHORT_COLLECTION, SHORT_REQUEST, sized

SOUNDCLOUD = "https://soundcloud.com/platform/kaytranada-boiler-room"

# -- links: store searches and the set at a moment ------------------------------------------------


def test_store_searches_drop_what_stores_do_not_name():
    links = set_sources.shop_links("Lumen ft. Ana", "Lasso (Original Mix) [ED BANGER]", "")
    assert links == {
        "beatport": "https://www.beatport.com/search?q=Lumen+Lasso",
        "bandcamp": "https://bandcamp.com/search?q=Lumen+Lasso&item_type=t",
        "traxsource": "https://www.traxsource.com/search?term=Lumen+Lasso",
    }
    moon = set_sources.shop_links("Armand Van Helden", "Full Moon (feat. Common)", "")
    assert moon["beatport"].endswith("?q=Armand+Van+Helden+Full+Moon")
    assert set_sources.shop_links("A", "B", "Original Mix")["beatport"].endswith("?q=A+B")
    # "&" can't end the query early; accents survive as UTF-8.
    corpo = set_sources.shop_links("Antdot & Maz", "Corpo e Canção", "Club Mix")
    assert corpo["traxsource"] == (
        "https://www.traxsource.com/search?term=Antdot+%26+Maz+Corpo+e+Can%C3%A7%C3%A3o+Club+Mix"
    )


@pytest.mark.parametrize(
    ("link", "seconds", "expected"),
    [
        (
            "https://youtu.be/cmgN8X7Pp_0?si=x",
            339,
            "https://www.youtube.com/watch?v=cmgN8X7Pp_0&t=339s",
        ),
        (
            "https://www.youtube.com/watch?v=cmgN8X7Pp_0&list=PL1&t=10",
            339.4,
            "https://www.youtube.com/watch?v=cmgN8X7Pp_0&t=339s",
        ),
        ("https://m.youtube.com/watch?v=abc", 5, "https://www.youtube.com/watch?v=abc&t=5s"),
        (SOUNDCLOUD, 3725, f"{SOUNDCLOUD}#t=1:02:05"),
        (SOUNDCLOUD + "/?in=x", 69, f"{SOUNDCLOUD}#t=1:09"),
        ("http://www.youtube.com/watch?v=abc", 5, None),
        ("https://evil.com/watch?v=abc", 5, None),
        ("https://youtube.com.evil.com/watch?v=abc", 5, None),
        ("https://www.youtube.com/watch?v=a%22b", 5, None),
        ("https://soundcloud.com", 5, None),
        ("https://www.youtube.com/watch?v=abc", None, None),
        ("https://www.youtube.com/watch?v=abc", float("nan"), None),
        (None, 5, None),
    ],
)
def test_a_time_links_to_that_moment_of_the_set(link, seconds, expected):
    assert set_sources.listen_url(link, seconds) == expected


# -- the set table ---------------------------------------------------------------------------------

# time | artist | title | version | how sure | what you own
KAYTRANADA_SET = """
00:00 | Kaytranada | Roots | Silent Treatment Intro | confirmed | owned
01:09 | Robert Glasper Experiment | Move Love | Kaytranada Edition | confirmed | missing
03:08 | KenLo Croqnote | Facteur Humidex | | likely | missing
03:55 | Flume | Holdin On | Kaytranada Edit | confirmed | owned
06:37 | SebastiAn | Arabest | | likely | owned
08:30 | Full Crate Feat. Papa Ghana | L'afrique | | confirmed | missing
10:32 | Kaytranada | At All | | confirmed | owned
12:46 | De La Soul | Me, Myself & I | | likely | owned
14:12 | Erykah Badu | I Want You | Kaytranada Remix | likely | missing
15:17 | Teedra Moses | Be Your Girl | Kaytranada Edit | likely | owned
17:06 | Common | I Want You | Kaytranada Edition | likely | owned
18:33 | Sharam Jey & Sirus Hood | Picture Picture | | confirmed | missing
19:52 | Indeep | Last Night A DJ Saved My Life | | likely | owned
21:24 | Busta Rhymes X Janet Jackson | What It's Gonna Be | Kaytranada Edition | confirmed | owned
24:10 | Pomo | So Fine | | likely | missing
26:00 | Bell Biv DeVoe | Poison | | likely | other_mix
28:36 | Janet Jackson | If | Kaytranada Remix | likely | owned
32:15 | JMSN | Love&pain | Kaytranada Remix | likely | missing
33:25 | Armand Van Helden | Full Moon (feat. Common) | | confirmed | missing
34:32 | Azealia Banks Feat. Pharrell Williams | ATM Jam | Kaytranada Edition | confirmed | owned
37:40 | Letherette | Dt | Dorian Concept Remix | likely | missing
39:17 | Modjo | Lady | Kaytranada Remix | likely | owned
"""
KAYTRANADA = [
    tuple(cell.strip() for cell in line.split("|")) for line in KAYTRANADA_SET.strip().splitlines()
]


def ledger_item(position, stamp, artist, title, version, sure, own, **extra) -> dict:
    """A request item as the ledger keeps it."""
    named = own != "id"
    item = {
        "position": position,
        "state": {"owned": "satisfied", "id": "unknown"}.get(own, "missing"),
        "input": {
            "kind": "named" if named else "unknown",
            "artist": artist if named else None,
            "title": title if named else None,
            "version": version if named else "",
            "label": None if named else "ID",
            "timestamp": stamp,
            "notes": "Shazam heard it once at " + stamp if sure == "confirmed" else "From MixesDB",
            "confidence": sure,
        },
        "candidates": [],
        "accepted": {"path": f"/music/{title}.mp3"} if own == "owned" else None,
    }
    if own == "other_mix":
        item["candidates"] = [
            {"identity_match": "different_version", "title": f"{title} (Radio Edit)"}
        ]
    item.update(extra)
    return item


def set_result(rows, *, link=SOUNDCLOUD, **changes) -> dict:
    items = [ledger_item(n, *row) for n, row in enumerate(rows, 1)]
    tracks = [set_track(item, link) for item in items]
    result = {
        "name": "Kaytranada Boiler Room Montreal DJ Set",
        "request_id": REQUEST,
        "source": {"url": link, "provider": "Soundcloud", "title": "Kaytranada Boiler Room"},
        "songs": len(items),
        "owned": sum(1 for item in items if item["state"] == "satisfied"),
        "tracks": tracks,
        "other_mix": sum(1 for track in tracks if track["own"] == "other_mix"),
        "identified": {
            "counts": {"tracks": len(items), "heard": 9, "listed_only": 13, "ids": 0},
            "recognizer": {"name": "shazam", "licensed": False},
            "listened_at": "2026-10-13T20:00:00+00:00",
        },
        "collection_id": COLLECTION,
        "playlist": "Kaytranada Boiler Room Montreal DJ Set",
        "rekordbox": {"status": "skipped", "reason_code": "NOT_REQUESTED", "installed": True},
    }
    return {**result, **changes}


def shown(name: str, result: dict, width: int, capsys) -> str:
    views.show(sized(width), name, {"ok": True, "result": result, "warnings": []})
    return capsys.readouterr().out


@pytest.mark.parametrize("width", [80, 100, 140])
def test_the_whole_set_reads_in_set_order_at_any_width(width, capsys):
    out = shown("set", set_result(KAYTRANADA), width, capsys)
    # The counts stay whole, beside the name or on the line under it.
    assert "12 of 22 owned · 1 in another mix · 9 missing" in out
    assert "The set (22)" in out and "Not in the crate" not in out
    rows = [line for line in out.splitlines() if re.match(r"^\s*\d+\s+\d\d:\d\d\s", line)]
    assert [int(line.split()[0]) for line in rows] == list(range(1, 23))
    # A name wraps; it is never cut short, and no column explains itself beside it.
    assert "…" not in out and "Why" not in out and "(Kaytranada" in out
    assert out.count("✓ owned") == 12 and out.count("✗ missing") == 9
    poison = next(line for line in rows if "Poison" in line)
    assert "≈ other mix:" in poison and "missing" not in poison and "Radio Edit" in out
    if width >= 140:
        assert "≈ other mix: Radio Edit" in poison
    # Why the likely lines are there, under the table, grouped.
    assert "From MixesDB: #3, #5, #8, #9, #10, #11, #13, #15, #16, #17, #18, #21, #22" in out
    assert "Likely and weak lines are worth a listen before you play them." in out
    assert "You own another mix of 1 song; djlib only counts the exact mix." in out
    assert "Exact artist/title/version" not in out and "window" not in out
    steps = [" ".join(line.split()[1:]) for line in out.split("Next\n", 1)[1].splitlines()]
    assert steps[:3] == [
        f"Put it in rekordbox djlib rekordbox push {SHORT_COLLECTION}",
        f"Or straight onto your USB djlib rekordbox usb {SHORT_COLLECTION}",
        f"Save the missing songs as a list djlib requests report {SHORT_REQUEST}",
    ]


def test_set_json_lists_every_track_with_what_you_own():
    tracks = set_result(KAYTRANADA)["tracks"]
    assert [track["position"] for track in tracks] == list(range(1, 23))
    poison = tracks[15]
    assert (poison["state"], poison["own"], poison["you_own"]) == (
        "missing",
        "other_mix",
        ["Radio Edit"],
    )
    assert poison["listen_url"] == f"{SOUNDCLOUD}#t=26:00"
    assert poison["shop"]["beatport"] == "https://www.beatport.com/search?q=Bell+Biv+DeVoe+Poison"
    assert tracks[0]["own"] == "owned" and tracks[0]["shop"] is None
    assert set(tracks[0]) >= {
        "position",
        "timestamp",
        "label",
        "state",
        "own",
        "confidence",
        "you_own",
        "notes",
        "matched_as",
        "listen_url",
        "shop",
    }


def test_an_id_shows_its_guess_greyed_and_owns_nothing(capsys):
    rows = [
        ("00:00", "Antdot", "Mirage", "", "confirmed", "owned"),
        ("48:30", "", "", "", "weak", "id"),
    ]
    result = set_result(rows)
    result["tracks"][1]["maybe"] = "Chris Fortier & Neil Kolo - All I Got"
    out = shown("set", result, 120, capsys)
    line = next(line for line in out.splitlines() if "48:30" in line)
    assert "ID — maybe Chris Fortier & Neil Kolo - All I Got" in line and "! weak" in line
    assert "owned" not in line and "missing" not in line
    assert "1 of 2 owned · 1 ID" in out and "unknown ID" not in out


def test_a_song_owned_under_another_credit_says_which(capsys):
    glasper = ("01:09", "Robert Glasper Experiment", "Move Love", "Kaytranada Edition")
    item = ledger_item(2, *glasper, "confirmed", "owned")
    item["accepted"]["matched_as"] = "Robert Glasper - Move Love (Kaytranada Edition)"
    track = set_track(item, SOUNDCLOUD)
    assert track["matched_as"] == "Robert Glasper - Move Love (Kaytranada Edition)"
    out = shown("set", {"name": "Set", "songs": 1, "owned": 1, "tracks": [track]}, 120, capsys)
    assert "✓ owned as Robert Glasper" in out


def test_a_shazam_history_lists_only_what_you_dont_own(capsys):
    rows = [
        ("", "Artist", f"Song {n}", "", None, "owned" if n % 40 == 0 else "missing")
        for n in range(1, 1001)
    ]
    result = set_result(rows, link=None, name="Shazam since 2019-03-02", shazam={"songs": 1000})
    out = shown("set", result, 100, capsys)
    assert "Not owned yet (975 of 1000)" in out
    assert len([line for line in out.splitlines() if "✗ missing" in line]) == 25
    assert f"… and 950 more: djlib requests report {SHORT_REQUEST}" in out
    assert "Time" not in out and "Sure" not in out  # Shazams have neither
    assert len(result["tracks"]) == 1000  # the JSON keeps every one


def test_without_rekordbox_installed_the_crate_is_the_next_step(capsys):
    result = set_result(KAYTRANADA[:2])
    result["rekordbox"]["installed"] = False
    out = shown("set", result, 120, capsys)
    steps = " ".join(out.split("Next\n", 1)[1].split())
    assert f"See the crate djlib crate {SHORT_COLLECTION}" in steps and "push" not in steps


def test_times_link_to_that_moment_where_the_terminal_can():
    console = Console(
        file=io.StringIO(),
        force_terminal=True,
        color_system="truecolor",
        width=120,
        theme=terminal.THEME,
    )
    term = sized(120)
    term.out = console
    views.set_view(term, set_result(KAYTRANADA[:2]))
    out = console.file.getvalue()
    assert "\x1b]8;" in out and f"{SOUNDCLOUD}#t=1:09" in out


def test_the_identify_screen_speaks_in_tracks_heard_and_ids(capsys):
    result = {
        "source": {"url": SOUNDCLOUD, "provider": "Soundcloud", "title": "Kaytranada"},
        "recognizer": {"name": "shazam", "licensed": False},
        "listened_at": "2026-10-13T20:00:00+00:00",
        "counts": {"tracks": 3, "heard": 1, "listed_only": 1, "ids": 1, "unidentified": 1},
        "tracks": [
            {
                "n": 1,
                "start": "00:00",
                "kind": "named",
                "artist": "A",
                "title": "One",
                "status": "confirmed",
                "evidence": "Shazam heard it once at 00:09",
                "listen_url": f"{SOUNDCLOUD}#t=0:00",
            },
            {
                "n": 2,
                "start": "03:08",
                "kind": "named",
                "artist": "B",
                "title": "Two",
                "status": "likely",
                "evidence": "From MixesDB",
            },
            {
                "n": 3,
                "start": "06:00",
                "kind": "unknown",
                "status": "unidentified",
                "evidence": "Listed as an ID in the description",
            },
        ],
        "unplaced": [{"label": "C - Three", "at": "05:39", "listen_url": f"{SOUNDCLOUD}#t=5:39"}],
        "next": [f"djlib set {SOUNDCLOUD} --no-rekordbox"],
        "notices": [],
    }
    out = shown("identify", result, 100, capsys)
    assert "3 tracks · 1 heard by Shazam · 1 from tracklists only · 1 ID" in out
    assert "Shazam · listened 13 Oct" in out and "unofficial" not in out
    assert "% heard" not in out and "unidentified" not in out and "○ ID" in out
    assert "Heard once, not placed" in out and "05:39  C - Three" in out
    steps = out.split("Next\n", 1)[1]
    assert steps.index("--no-rekordbox") < steps.index("Put it in rekordbox")
    # The once-per-workspace notice fires on this listen: the row says so too.
    fresh = shown(
        "identify", {**result, "notices": [identification.UNOFFICIAL_SHAZAM]}, 100, capsys
    )
    assert "unofficial; may slow down or stop working" in fresh


def test_requests_get_marks_another_mix_and_ids_in_dj_words(capsys):
    items = [ledger_item(n, *row) for n, row in enumerate(KAYTRANADA[14:16], 1)]
    items.append(ledger_item(3, "40:00", "", "", "", "unidentified", "id"))
    items[2]["input"]["label"] = "ID - ID"
    request = {
        "request_id": REQUEST,
        "name": "Friday",
        "total_items": 3,
        "counts": {"missing": 2, "unknown": 1},
        "unresolved_items": 3,
        "items": items,
    }
    out = shown("requests get", request, 120, capsys)
    assert "1 in another mix  ·  1 missing  ·  1 ID" in out
    poison = next(line for line in out.splitlines() if "Poison" in line)
    assert "≈ other mix" in poison and "you own: Radio Edit" in poison
    assert "ID - ID" not in out and "unknown ID" not in out
    assert f"djlib requests report {SHORT_REQUEST}" in out


# -- through the CLI and the coordinator ----------------------------------------------------------

LINK = "https://www.youtube.com/watch?v=abcdefghijk"


@pytest.fixture
def friday(application, library_http, audio_factory, tmp_path, monkeypatch):
    sources = [audio_factory(f"friday-{n}.wav", frequency=520 + 70 * n) for n in range(2)]
    build(library_http, sources, [("Nia Okoro", "Slow Burn"), ("Lumen", "Halo (Radio Edit)")])
    monkeypatch.setattr(rekordbox_mac, "installed", lambda: None)
    tracklist = tmp_path / "friday.txt"
    tracklist.write_text(
        "Friday — Warm-up\n"
        "[00:00] Nia Okoro - Slow Burn\n"
        "[03:10] Lumen - Halo (Dub)\n"
        "[06:20] ID - ID\n"
        "[09:30] Someone - Not Owned\n",
        encoding="utf-8",
    )
    workspace = ["--workspace", str(application.workspace.root)]

    def djlib(*arguments):
        reply = CliRunner().invoke(cli_app, [*workspace, *map(str, arguments)])
        assert reply.exit_code == 0, reply.output
        return json.loads(reply.stdout)["result"]

    return djlib, tracklist


def test_set_json_adds_the_whole_set_and_keeps_missing_as_it_was(friday):
    djlib, tracklist = friday
    result = djlib("set", tracklist, "--source", LINK, "--no-rekordbox")
    assert (result["songs"], result["owned"], result["other_mix"]) == (4, 1, 1)
    tracks = result["tracks"]
    assert len(tracks) == result["songs"]
    assert [track["own"] for track in tracks] == ["owned", "other_mix", "id", "missing"]
    # A pasted tracklist times its IDs; the set's link plays from there.
    assert [track["listen_url"] for track in tracks] == [None, None, f"{LINK}&t=380s", None]
    assert [track["shop"] is not None for track in tracks] == [False, True, False, True]
    assert tracks[2]["label"] == "ID" and tracks[2]["maybe"] is None
    # The lists a15 gave stay exactly as they were.
    assert result["missing"] == [
        {
            "position": 2,
            "label": "Lumen - Halo (Dub)",
            "state": "missing",
            "you_own": ["Radio Edit"],
        },
        {"position": 3, "label": "ID - ID @ 06:20", "state": "unknown", "you_own": []},
        {"position": 4, "label": "Someone - Not Owned", "state": "missing", "you_own": []},
    ]
    assert result["owned_to_check"] == []
    assert result["rekordbox"] == {
        "status": "skipped",
        "reason": "not requested (--no-rekordbox)",
        "reason_code": "NOT_REQUESTED",
        "installed": False,
    }


def test_an_identified_set_times_every_song_and_keeps_an_ids_guess_apart(
    application, library_http, audio_factory, monkeypatch
):
    setup = fakes(monkeypatch)
    setup.page["description"] = LISTED
    build(library_http, [audio_factory("halo.wav", frequency=330)], [("Lumen", "Halo")])
    monkeypatch.setattr(rekordbox_mac, "installed", lambda: None)
    reply = identify_invoke(application, "set", URL, "--no-rekordbox")
    tracks = json.loads(reply.stdout)["result"]["tracks"]
    assert [track["own"] for track in tracks] == ["owned", "id", "missing"]
    assert all(track["listen_url"].startswith(f"{URL}&t=") for track in tracks)
    assert tracks[1]["label"] == "ID" and tracks[1]["maybe"] == "Bicep - Glue"
    pretty = identify_invoke(application, "set", URL, "--no-rekordbox", pretty=True).stdout
    assert "ID — likely Bicep - Glue" in pretty and "The set (3)" in pretty


# -- next steps that paste and work ------------------------------------------------------------

PLACEHOLDERS = ("TRACKLIST.txt", "PATH", "FOLDER", "wanted.json", "FILE")


@pytest.mark.parametrize(
    ("name", "result"),
    [
        ("library", {"tracks": []}),
        ("crates", {"collections": []}),
        ("requests list", {"requests": []}),
        ("jobs list", {"jobs": []}),
        ("roots list", {"allowed_roots": []}),
        ("init", {"workspace": "/w", "config": {"allowed_roots": []}}),
        ("init", {"workspace": "/w", "config": {"allowed_roots": ["/a", "/b"]}}),
        ("use", {"workspace": "/w", "initialized": False}),
        ("status", {"workspace": "/w", "tracks": 20, "with_bpm": 20, "with_key": 20}),
        (
            "jobs get",
            {"job_id": "job_1", "kind": "scan", "state": "completed", "counts": {"succeeded": 3}},
        ),
    ],
)
def test_empty_screens_suggest_commands_that_paste_as_they_are(name, result, capsys):
    out = shown(name, result, 120, capsys)
    steps = out.split("Next\n", 1)[1]
    assert not [word for word in PLACEHOLDERS if word in steps], steps


def test_a_first_checked_set_or_recent_shazams_come_first_on_status(capsys):
    out = shown("status", {"tracks": 20, "shazam_history_found": True}, 120, capsys)
    steps = out.split("Next\n", 1)[1]
    assert "Copy a set link or tracklist, then  djlib set paste" in steps
    assert re.search(
        r"Your recent Shazams\s+djlib set shazam:\d{4}-\d\d-\d\d --no-rekordbox", steps
    )


def test_errors_suggest_the_real_folder_and_the_first_step(application, tmp_path, capsys):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(AppError) as refused:
        application.workspace.authorize(str(outside), directory=True)
    assert refused.value.details == {"path": str(outside.resolve())}
    terminal.render_error(sized(200), refused.value.as_dict())
    err = capsys.readouterr().err
    assert f"djlib roots add {terminal.shell_path(outside.resolve())}" in err
    terminal.render_error(sized(200), {"code": "WORKSPACE_REQUIRED", "message": "Not yet."})
    err = capsys.readouterr().err
    assert "djlib init --allow-root" in err and "PATH" not in err


def test_listening_to_nothing_named_suggests_pasting_a_tracklist():
    with pytest.raises(AppError) as nothing:
        identify_cli.identified_request({"source": {"url": LINK}, "tracklist": {}}, "Set")
    assert nothing.value.details["next"] == f"djlib set '{LINK}' --tracklist paste"


async def test_assistants_check_ownership_without_making_a_playlist(application):
    async with Client(mcp_server.build_server(application.workspace)) as client:
        tools = {tool.name: tool.description for tool in (await client.list_tools()).tools}
    for name in ("djlib_identify_set", "djlib_shazam_history"):
        assert "--no-rekordbox" in tools[name]
    found = {"source": {"url": LINK}, "tracks": [], "coverage": {"percent": 90}}
    assert identification.next_steps(found)[0] == f"djlib set '{LINK}' --no-rekordbox"
