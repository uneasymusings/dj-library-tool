"""What a DJ sees after `djlib set` and `djlib identify`, and the shopping list after
`djlib requests report`: the whole set in set order, owned/missing/other mix/ID, store searches
and times that play the set from that moment."""

import io
import json
import re
from pathlib import Path

import pytest
from mcp import Client
from rich.console import Console
from typer.testing import CliRunner

from djlib.application import identification
from djlib.application.set_identification import build_tracklist
from djlib.domain.errors import AppError
from djlib.interfaces import identify_cli, library_cli, mcp_server, set_sources, terminal, views
from djlib.interfaces.cli import app as cli_app
from djlib.interfaces.rekordbox_cli import set_track
from djlib.native import rekordbox_mac
from djlib.sources import web
from tests.test_headline_workflow import build
from tests.test_identification import URL
from tests.test_identify_cli import LISTED, fakes
from tests.test_identify_cli import invoke as identify_invoke
from tests.test_set_identification import silence, song
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
    if own == "maybe":
        item["candidates"] = [
            {
                "identity_match": "similar_credits",
                "recording_id": "recording_1",
                "asset_revision_id": "revision_1",
                "artist": artist.split()[0] + " " + artist.split()[1],
                "title": title,
                "version": version,
            }
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
    assert "≈ Radio Edit" in poison and "missing" not in poison
    # Why the likely lines are there, under the table, grouped.
    assert "From MixesDB: #3, #5, #8, #9, #10, #11, #13, #15, #16, #17, #18, #21, #22" in out
    assert "Likely and weak lines are worth a listen before you play them." in out
    assert "You own another mix of 1 song; djlib only counts the exact mix." in out
    assert "Exact artist/title/version" not in out and "window" not in out
    steps = [" ".join(line.split()[1:]) for line in out.split("Next\n", 1)[1].splitlines()]
    assert steps[:3] == [
        f"Put it in rekordbox djlib rekordbox push {SHORT_COLLECTION}",
        f"Or straight onto your USB djlib rekordbox usb {SHORT_COLLECTION}",
        f"Your shopping list (9) djlib requests report {SHORT_REQUEST}",
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
    # Counted as an ID in the headline, and said so in the Sure column: the guess is the name's.
    assert "ID — maybe Chris Fortier & Neil Kolo - All I Got" in line and "○ ID" in line
    assert "weak" not in line and "worth a listen" not in out
    assert "owned" not in line and "missing" not in line
    assert "1 of 2 owned · 1 ID" in out and "unknown ID" not in out


def test_a_song_owned_under_another_credit_says_which(capsys):
    glasper = ("01:09", "Robert Glasper Experiment", "Move Love", "Kaytranada Edition")
    item = ledger_item(2, *glasper, "confirmed", "owned")
    item["accepted"]["matched_as"] = "Robert Glasper - Move Love (Kaytranada Edition)"
    track = set_track(item, SOUNDCLOUD)
    assert track["matched_as"] == "Robert Glasper - Move Love (Kaytranada Edition)"
    out = shown("set", {"name": "Set", "songs": 1, "owned": 1, "tracks": [track]}, 120, capsys)
    row = next(line for line in out.splitlines() if "Move Love" in line)
    # The column stays narrow; the spelling the file matched goes under the table.
    assert row.rstrip().endswith("✓ owned")
    assert "Owned under another spelling: #2 Robert Glasper" in out
    # With a song whose title is spelt otherwise too, every one gets its whole label.
    moon = ledger_item(
        19, "33:25", "Armand Van Helden", "Full Moon (feat. Common)", "", "", "owned"
    )
    moon["accepted"]["matched_as"] = "Armand Val Helden Feat. Common - Full Moon"
    both = [track, set_track(moon, SOUNDCLOUD)]
    out = shown("set", {"name": "Set", "songs": 2, "owned": 2, "tracks": both}, 160, capsys)
    assert (
        "Owned under another spelling: #2 Robert Glasper - Move Love (Kaytranada Edition) · "
        "#19 Armand Val Helden Feat. Common - Full Moon"
    ) in " ".join(out.split())


def test_a_song_maybe_yours_under_a_shorter_credit_is_off_the_shopping_list(capsys):
    """A Shazam list: the file is tagged "Robert Glasper". Shown as maybe yours, named under
    the table, counted apart, and the next step is checking it, not buying it."""
    rows = [
        ("", "Antdot", "Mirage", "", "", "owned"),
        ("", "Robert Glasper Experiment", "Move Love", "Kaytranada Edition", "", "maybe"),
    ]
    out = shown("set", set_result(rows, link=None), 100, capsys)
    assert "1 of 2 owned · 1 maybe yours" in out and "missing" not in out
    assert "? maybe yours" in out
    assert "Maybe yours under another name: #2 Robert Glasper - Move Love (Kaytranada " in out
    assert "Check the songs that may be yours (1)" in out and "shopping list" not in out
    # At 80 columns the words stay on one line; the track's name wraps instead.
    timed = [("00:00", *rows[0][1:]), ("01:09", *rows[1][1:])]
    narrow = shown("set", set_result(timed), 80, capsys)
    assert any(line.rstrip().endswith("? maybe yours") for line in narrow.splitlines()), narrow
    found = library_cli.shopping([ledger_item(n, *row) for n, row in enumerate(rows, 1)])
    assert found["to_buy"] == []
    assert found["maybe_yours"][0]["maybe_owned"] == (
        "Robert Glasper - Move Love (Kaytranada Edition)"
    )
    text = library_cli.shopping_text("Shazam", found)
    assert "Maybe yours already: check before you buy (1)" in text


def test_the_crate_line_says_how_many_files_the_crate_holds(capsys):
    """A song the set lists twice is one file in the crate."""
    out = shown("set", set_result(KAYTRANADA[:3], crate_tracks=2), 100, capsys)
    assert "✓ 2 tracks, in set order" in out


def test_a_shazam_history_lists_only_what_you_dont_own(capsys):
    rows = [
        ("", "Artist", f"Song {n}", "", None, "owned" if n % 40 == 0 else "missing")
        for n in range(1, 1001)
    ]
    result = set_result(rows, link=None, name="Shazam since 2019-03-02", shazam={"songs": 1000})
    out = shown("set", result, 100, capsys)
    # Counts read with thousands separators, as the Shazam lines beside them do.
    assert "25 of 1,000 owned · 975 missing" in out
    assert "Not owned yet (975 of 1,000)" in out and "Your shopping list (975)" in out
    assert len([line for line in out.splitlines() if "✗ missing" in line]) == 25
    assert f"… and 950 more: djlib requests report {SHORT_REQUEST}" in out
    assert "Time" not in out and "Sure" not in out  # Shazams have neither
    assert len(result["tracks"]) == 1000  # the JSON keeps every one


def test_owned_songs_waiting_on_a_pick_are_counted_and_listed(capsys):
    rows = [*KAYTRANADA[:6], ("", "Lumen", "Halo", "", None, "owned")]
    items = [ledger_item(n, *row) for n, row in enumerate(rows, 1)]
    # Two copies of the exact mix, and a file on a drive that isn't plugged in.
    items[0].update(state="ambiguous", accepted=None)
    items[3].update(state="unavailable", accepted=None)
    tracks = [set_track(item, SOUNDCLOUD) for item in items]
    assert [(track["own"], track["in_crate"]) for track in tracks[:4:3]] == [
        ("owned", False),
        ("owned", False),
    ]
    assert tracks[4]["in_crate"] is True and tracks[1]["in_crate"] is False
    result = set_result(rows, tracks=tracks, owned=2, other_mix=0)
    out = shown("set", result, 100, capsys)
    # Every song is counted once: 2 + 2 + 3 = 7.
    assert "2 of 7 owned · 2 to check · 3 missing" in out
    assert "! owned, pick a copy" in out and "! owned, file unavailable" in out
    steps = [" ".join(line.split()[1:]) for line in out.split("Next\n", 1)[1].splitlines()]
    assert steps[:2] == [
        "Pick which copy to use (1) djlib ui",
        f"Once the unavailable files are back, check again djlib requests refresh {SHORT_REQUEST}",
    ]
    # A long list (a Shazam history) keeps them, under a heading that fits them.
    many = rows + [("", "Artist", f"Song {n}", "", None, "owned") for n in range(60)]
    items += [ledger_item(n, *row) for n, row in enumerate(many[7:], 8)]
    big = set_result(many, tracks=[set_track(item) for item in items], shazam={"songs": 67})
    out = shown("set", big, 120, capsys)
    assert "Not in the crate yet (5 of 67)" in out
    assert "pick a copy" in out and "file unavailable" in out
    # No crate at all: the songs marked ! are why.
    none = set_result(rows[:1], tracks=tracks[:1], owned=0, collection_id=None)
    assert "none yet: the songs marked ! need a look first" in shown("set", none, 120, capsys)


def test_a_pasted_tracklist_that_times_only_its_ids_has_no_time_column(capsys):
    rows = [
        ("", "Antdot", "Mirage", "", None, "owned"),
        ("06:20", "", "", "", None, "id"),
        ("", "Modjo", "Lady", "Kaytranada Remix", None, "missing"),
    ]
    out = shown("set", set_result(rows, link=LINK), 100, capsys)
    assert "Time" not in out and "ID @ 06:20" in out
    timed = shown("set", set_result(KAYTRANADA[:2] + rows[1:2]), 100, capsys)
    assert "Time" in timed and "ID @" not in timed


def test_at_80_columns_most_songs_keep_to_one_line(capsys):
    def table(width):
        out = shown("set", set_result(KAYTRANADA), width, capsys)
        return out.split("The set (22)\n", 1)[1].split("\n\n", 1)[0].splitlines()[2:]

    narrow = table(80)
    # Two columns between cells, and a confirmed line is just its tick.
    assert len(narrow) - 22 <= 4, "\n".join(narrow)
    assert " 1  00:00  Kaytranada - Roots (Silent Treatment Intro)" in narrow[0]
    assert "✓ confirmed" not in "\n".join(narrow) and "◐ likely" in "\n".join(narrow)
    assert "✓ confirmed" in "\n".join(table(100))


def test_another_mix_with_no_mix_name_says_so(capsys):
    item = ledger_item(1, "", "Bell Biv DeVoe", "Poison", "Extended Mix", None, "missing")
    item["candidates"] = [{"identity_match": "different_version", "title": "Poison"}]
    track = set_track(item)
    assert track["own"] == "other_mix" and track["you_own"] == ["Poison"]  # the JSON as read
    result = set_result([], tracks=[track], songs=1, owned=0, other_mix=1, collection_id=None)
    out = shown("set", result, 120, capsys)
    assert "≈ no mix name" in out and "≈ Poison" not in out
    # The headline counts it in another mix; the crate row agrees.
    assert "0 of 1 owned · 1 in another mix" in out
    assert "no exact mixes in your library yet" in out and "none of these songs" not in out
    request = {"request_id": REQUEST, "name": "Friday", "total_items": 1, "items": [item]}
    assert "you own: no mix name" in shown("requests get", request, 120, capsys)


def test_the_listen_note_is_for_likely_and_weak_lines_not_ids(capsys):
    rows = [
        ("00:00", "A", "One", "", "confirmed", "owned"),
        ("03:00", "", "", "", "unidentified", "id"),
    ]
    out = shown("set", set_result(rows), 120, capsys)
    assert "#2" in out and "worth a listen" not in out
    unsure = shown(
        "set", set_result([*rows, ("06:00", "B", "Two", "", "weak", "missing")]), 120, capsys
    )
    assert "Likely and weak lines are worth a listen before you play them." in unsure


def test_a_long_list_you_own_every_song_of_keeps_its_heading(capsys):
    rows = [("", "Artist", f"Song {n}", "", None, "owned") for n in range(1, 71)]
    result = set_result(rows, link=None, name="Shazam since 2026-09-01", shazam={"songs": 70})
    out = shown("set", result, 100, capsys)
    assert "Your Shazams (70)" in out and "You own every one of them." in out
    assert "Not owned yet" not in out


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
        legacy_windows=False,  # a Windows runner's stdout is no VT console
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
    # Heard in one window here and one there: twice, not once.
    twice = {**result, "unplaced": [{**result["unplaced"][0], "also_at": ["08:09"]}]}
    again = shown("identify", twice, 100, capsys)
    assert "Heard briefly, not placed" in again and "once" not in again.split("Heard")[-1]
    assert "05:39  C - Three  again at 08:09" in again
    steps = out.split("Next\n", 1)[1]
    assert steps.index("--no-rekordbox") < steps.index("Put it in rekordbox")
    # The once-per-workspace notice fires on this listen: the row says so too.
    fresh = shown(
        "identify", {**result, "notices": [identification.UNOFFICIAL_SHAZAM]}, 100, capsys
    )
    assert "unofficial; may slow down or stop working" in fresh


def test_the_summary_counts_songs_and_keeps_seconds_heard_in_seconds():
    probes = [*song(0, 6, "Lumen", "Halo"), *map(silence, range(180, 1380, 30))]
    tracklist = build_tracklist(duration=1600, probes=[*probes, *song(1380, 6, "Ana", "Rain")])
    assert tracklist["coverage"]["seconds_heard"] == 324.0
    found = identification.summary({"source": {"url": SOUNDCLOUD}, "tracklist": tracklist})
    assert found["coverage"]["seconds_heard"] == 324.0  # seconds, as before
    assert found["coverage"]["percent_heard"] == 20
    assert {key: found["counts"][key] for key in ("tracks", "heard", "listed_only", "ids")} == {
        "tracks": 3,
        "heard": 2,
        "listed_only": 0,
        "ids": 1,
    }
    assert found["tracks"][2]["listen_url"] == f"{SOUNDCLOUD}#t=23:00"


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
    assert f"Your shopping list djlib requests report {SHORT_REQUEST}" in " ".join(out.split())


# -- the shopping list -----------------------------------------------------------------------------


def test_the_shopping_list_prints_each_store_link_in_one_piece(capsys):
    to_buy = [
        {
            "position": 2,
            "timestamp": "01:09",
            "label": "Robert Glasper Experiment - Move Love (Kaytranada Edition)",
            "shop": set_sources.shop_links(
                "Robert Glasper Experiment", "Move Love", "Kaytranada Edition"
            ),
        },
    ]
    result = {
        "request_id": REQUEST,
        "name": "Kaytranada Boiler Room Montreal DJ Set",
        "to_buy": to_buy,
        "other_mixes": [
            {
                "position": 16,
                "timestamp": "26:00",
                "label": "Bell Biv DeVoe - Poison",
                "you_own": ["Radio Edit"],
            }
        ],
        "ids": [{"position": 3, "timestamp": None}],
        "shopping_list": "/w/exports/requests/r/Kaytranada - to buy.txt",
        "report_path": "/w/exports/requests/r/missing-r1.json",
    }
    out = shown("requests report", result, 60, capsys)
    many = {**result, "to_buy": to_buy * 30}
    assert "… and 5 more in /w/exports/requests/r/Kaytranada - to buy.txt" in shown(
        "requests report", many, 60, capsys
    )
    assert out.startswith("✓ Kaytranada Boiler Room Montreal DJ Set — 1 to buy")
    lines = [line.strip() for line in out.splitlines()]
    for store, link in to_buy[0]["shop"].items():
        assert f"{views.STORE_NAMES[store]}{' ' * (12 - len(store))}{link}" in lines
    assert "26:00 Bell Biv DeVoe - Poison — you have: Radio Edit" in " ".join(out.split())
    assert "IDs, no name yet: #3" in out
    assert f"djlib requests refresh {SHORT_REQUEST}" in out


def test_the_shopping_list_path_prints_in_one_piece(capsys, monkeypatch):
    saved = "/w/Shopping lists/Kaytranada Boiler Room Montreal - to buy.txt"
    result = {"request_id": REQUEST, "name": "Kaytranada", "to_buy": [], "shopping_list": saved}
    monkeypatch.setattr(views.sys, "platform", "darwin")
    out = shown("requests report", result, 60, capsys)
    assert f"  Saved to  {saved}" in out.splitlines()
    # On a Mac, a step opens it.
    assert f"open {terminal.shell_path(saved)}" in out
    monkeypatch.setattr(views.sys, "platform", "win32")
    assert "open " not in shown("requests report", result, 60, capsys)


# -- status: rekordbox where there is one ------------------------------------------------------


def status_with(**changes) -> dict:
    crate = {"collection_id": COLLECTION, "name": "Kaytranada", "tracks": 12, "in_rekordbox": None}
    return {"tracks": 20, "with_bpm": 20, "with_key": 20, "recent_collections": [crate], **changes}


def test_status_offers_rekordbox_only_where_it_is_installed(capsys):
    out = shown("status", status_with(rekordbox_checked=False, rekordbox_app=False), 120, capsys)
    assert "rekordbox" not in out.replace("djlib rekordbox", "")
    assert "push" not in out and "closed" not in out
    closed = status_with(rekordbox_checked=False, rekordbox_app=True, rekordbox_open=False)
    out = shown("status", closed, 120, capsys)
    # It can't tell, so it says so once, not "not in rekordbox yet" on each line.
    assert "not in rekordbox yet" not in out and f"djlib rekordbox push {SHORT_COLLECTION}" in out
    assert "(rekordbox is closed, so djlib can't tell which are in it)" in out
    # rekordbox read and the crate isn't there: then it says so.
    read = status_with(rekordbox_checked=True, rekordbox_app=True)
    read["recent_collections"] = [
        {**row, "in_rekordbox": False} for row in read["recent_collections"]
    ]
    assert "not in rekordbox yet" in shown("status", read, 120, capsys)
    # Open, but its playlists out of reach: djlib doesn't claim it's closed.
    opened = status_with(rekordbox_checked=False, rekordbox_app=True, rekordbox_open=True)
    out = shown("status", opened, 120, capsys)
    assert "closed" not in out and "can't see rekordbox's playlists right now" in out
    assert f"djlib rekordbox push {SHORT_COLLECTION}" in out


def test_status_knows_whether_rekordbox_is_open(application, library_http, monkeypatch):
    from djlib.interfaces import rekordbox_cli

    monkeypatch.setattr(rekordbox_cli, "rekordbox_playlists", lambda: None)
    monkeypatch.setattr(rekordbox_mac, "installed", lambda: {"path": "/Applications/rekordbox 7"})
    monkeypatch.setattr(rekordbox_mac, "process_running", lambda: False)
    workspace = ["--workspace", str(application.workspace.root)]
    reply = CliRunner().invoke(cli_app, [*workspace, "status"])
    result = json.loads(reply.stdout)["result"]
    assert (result["rekordbox_app"], result["rekordbox_open"]) == (True, False)
    monkeypatch.setattr(rekordbox_mac, "installed", lambda: None)
    result = json.loads(CliRunner().invoke(cli_app, [*workspace, "status"]).stdout)["result"]
    assert result["rekordbox_app"] is False and "rekordbox_open" not in result


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
    # A pasted tracklist times its songs; the set's link plays each from there.
    assert [track["listen_url"] for track in tracks] == [
        f"{LINK}&t=0s",
        f"{LINK}&t=190s",
        f"{LINK}&t=380s",
        f"{LINK}&t=570s",
    ]
    assert [track["timestamp"] for track in tracks] == ["00:00", "03:10", "06:20", "09:30"]
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
    # The tally in words: `missing` above also lists other mixes and IDs.
    assert result["counts"] == {
        "owned": 1,
        "missing": 1,
        "other_mix": 1,
        "ids": 1,
        "to_check": 0,
        "maybe": 0,
    }
    assert result["crate_tracks"] == 1
    # The set keeps its link, for the review page's badge and links into the set.
    saved = djlib("requests", "get", result["request_id"])
    assert saved["source"] == {"url": LINK, "provider": "YouTube", "title": None, "uploader": None}
    assert saved["crate"]["collection_id"] == result["collection_id"]
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


def test_requests_report_is_a_shopping_list_saved_beside_the_report(friday):
    djlib, tracklist = friday
    request_id = djlib("set", tracklist, "--no-rekordbox")["request_id"]
    report = djlib("requests", "report", request_id)
    assert report["report_path"].endswith(".json") and report["unresolved_items"] == 3
    assert [song["label"] for song in report["to_buy"]] == ["Someone - Not Owned"]
    assert report["to_buy"][0]["shop"] == set_sources.shop_links("Someone", "Not Owned")
    assert [song["label"] for song in report["other_mixes"]] == ["Lumen - Halo (Dub)"]
    assert report["ids"] == [{"position": 3, "timestamp": "06:20"}]
    saved = report["shopping_list"]
    assert saved.endswith("Friday — Warm-up - to buy.txt")
    # In djlib's folder, under a name a DJ finds in Finder.
    assert Path(saved).parent.name == "Shopping lists"
    text = Path(saved).read_text(encoding="utf-8")
    # Each song by its time in the set, else its number.
    assert text.splitlines()[:3] == [
        "Friday — Warm-up — 1 to buy",
        "",
        "09:30  Someone - Not Owned",
    ]
    assert "    Beatport    https://www.beatport.com/search?q=Someone+Not+Owned\n" in text
    assert "You own another mix (1)\n03:10  Lumen - Halo (Dub) — you have: Radio Edit" in text
    assert text.endswith("IDs, no name yet: 06:20\n")
    # Again: the same file, rewritten, and no second one.
    again = djlib("requests", "report", request_id)
    folder = Path(saved).parent
    assert again["shopping_list"] == saved and len(list(folder.glob("*to buy.txt"))) == 1
    # The request list gives the same store searches for each song you don't own.
    items = djlib("requests", "get", request_id)["items"]
    assert [("shop" in item) for item in items] == [False, True, False, True]


def test_sets_named_alike_keep_their_own_shopping_lists(friday, tmp_path):
    """Two sets called Friday (and one FRIDAY: Macs and Windows ignore case) each keep their
    own shopping list: the first one's is “Friday - to buy.txt”, the others' add the set's
    code, whichever is reported first."""
    djlib, _ = friday
    songs = {
        "a": "Friday\nLumen - Halo\nBicep - Glue\n",
        "b": "Friday\nNia Okoro - Slow Burn\nFloating Points - Silhouettes\n",
        "c": "FRIDAY\nRival Consoles - Persona\nBicep - Atlas\n",
    }
    saved = {}
    for key, text in songs.items():
        path = tmp_path / f"{key}.txt"
        path.write_text(text, encoding="utf-8")
        saved[key] = djlib("set", path, "--no-rekordbox")["request_id"]
    for key in ("c", "a", "b", "a"):
        path = Path(djlib("requests", "report", saved[key])["shopping_list"])
        code = saved[key].split("_")[1][:8]
        named = {"a": "Friday - to buy.txt", "b": f"Friday ({code}) - to buy.txt"}
        assert path.name == named.get(key, f"FRIDAY ({code}) - to buy.txt")
        assert songs[key].splitlines()[2] in path.read_text(encoding="utf-8")
    assert len(list(path.parent.glob("*to buy.txt"))) == 3


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
    assert f"Copy a set link or tracklist, then  {web.copied()}" in steps
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
    first = f"djlib init {terminal.shell_path(Path.home() / 'Music')}"  # ~/Music off Windows
    assert first in err and "djlib scan" in err and "PATH" not in err


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
