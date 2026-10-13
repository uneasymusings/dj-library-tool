"""One tracklist from audio recognition, published tracklists and comments.

The regression data is a real scan of a 62-minute Keinemusik set: Shazam on a 12 s window
every 30 s (tests/data/keinemusik-scan.jsonl) and the set's published description.
"""

import json
from pathlib import Path

import pytest

from djlib.application.set_identification import (
    build_tracklist,
    clock,
    display_label,
    listed_tracklist,
    request_items,
    segments,
    tracklist_text,
)
from djlib.application.tracklists import parse_tracklist
from djlib.domain.request_contracts import RequestCreate

DATA = Path(__file__).parent / "data"
SET_LENGTH = 62 * 60
SET_URL = "https://www.youtube.com/watch?v=keinemusik"
SINGLE_WINDOW = {"Slacker", "128 MB of Netsex", "Taj", "People Say (Tech House Edit)"}


def scan_probes() -> list[dict]:
    rows = (DATA / "keinemusik-scan.jsonl").read_text(encoding="utf-8").splitlines()
    probes = []
    for row in map(json.loads, rows):
        match = None
        if row["title"]:
            match = {
                "provider": "shazam",
                "track_id": row["key"],
                "artist": row["artist"],
                "title": row["title"],
            }
        probes.append({"at": row["t"], "window": 12, "match": match})
    return probes


def description() -> dict:
    text = (DATA / "keinemusik-description.txt").read_text(encoding="utf-8")
    items, skipped = parse_tracklist(text)
    assert skipped == []
    return {"source": "description", "entries": [item.model_dump() for item in items]}


def heard(at: float, artist: str, title: str, **match) -> dict:
    return {
        "at": float(at),
        "window": 12.0,
        "match": {"provider": "shazam", "artist": artist, "title": title, **match},
        "error": None,
    }


def silence(at: float) -> dict:
    return {"at": float(at), "window": 12.0, "match": None, "error": None}


def song(start: int, windows: int, artist: str, title: str, **match) -> list[dict]:
    return [heard(start + 30 * step, artist, title, **match) for step in range(windows)]


def listing(source: str, *lines: str) -> dict:
    return listed_tracklist(source, "\n".join(lines))


def summary(tracks: list[dict]) -> list[tuple]:
    return [(clock(t["start"]), t["title"] or "ID", t["status"], t["approx"]) for t in tracks]


def valid_request(items: list[dict]) -> RequestCreate:
    return RequestCreate.model_validate({"name": "Set", "items": items, "idempotency_key": "k"})


@pytest.fixture(scope="module")
def keinemusik() -> dict:
    return build_tracklist(duration=SET_LENGTH, probes=scan_probes(), listed=[description()])


@pytest.fixture(scope="module")
def keinemusik_heard_only() -> dict:
    return build_tracklist(duration=SET_LENGTH, probes=scan_probes(), listed=[])


def test_keinemusik_scan_groups_into_songs():
    found = segments(scan_probes())
    labels = [(clock(s["start"]), s["artist"], s["title"], s["strength"]) for s in found]
    assert labels == [
        ("06:00", "Antdot", "Mirage", "strong"),
        ("12:30", "Antdot, BAKKA (BR) & Miguel Ante", "Inner Wars (feat. Rona Ray)", "strong"),
        ("17:00", "Fahlberg", "Bad Love", "strong"),
        ("26:00", "Antdot, Maz & Letícia Fialho", "Corpo e Canção", "strong"),
        ("31:30", "Antdot & Mozambo", "Stay", "strong"),
        ("35:00", "Maz, Antdot & Ayrton Senna", "Emotions", "strong"),
        ("40:00", "Q-Force", "Slacker", "weak"),
        ("47:30", "Sterling Infinity", "128 MB of Netsex", "weak"),
        ("50:30", "Chris Fortier & Neil Kolo", "All I Got", "medium"),
        ("51:30", "Supermini & Frankie Romano", "Celebration", "strong"),
        ("56:00", "BLR & Rave & Crave", "Taj", "weak"),
        ("56:30", "Paolo Rocco", "People Say (Tech House Edit)", "weak"),
        ("57:00", "Mozambo & Antdot", "Wish I Didn't Miss You", "strong"),
    ]
    mirage, *_, wish = found
    # Shazam's DJ-mix copy ("Antdot Mirage (Stif Mayer) [Mixed]") is the same song; the release
    # names it. Two windows of another song inside it are bridged, not a song of their own.
    assert {label["artist"] for label in mirage["labels"]} == {"Stif Mayer", "Antdot"}
    assert (mirage["start"], mirage["end"], mirage["hits"]) == (360.0, 732.0, 11)
    assert [(i["at"], i["title"]) for i in mirage["interruptions"]] == [
        (600.0, "Back2back"),
        (630.0, "Back2back"),
    ]
    # Angie Stone's original, sampled in the Antdot & Mozambo track, merges into it.
    assert [(label["artist"], label["hits"]) for label in wish["labels"]] == [
        ("Mozambo & Antdot", 6),
        ("Angie Stone", 4),
    ]
    assert wish["hits"] == 10
    celebration = found[9]
    assert celebration["version"] == "Antdot & Maz Edit"
    assert {i["track_id"] for i in celebration["ids"]} == {"875125848", "609627160"}
    assert json.loads(json.dumps(found)) == found


def test_keinemusik_set_with_its_description(keinemusik):
    tracks = keinemusik["tracks"]
    assert summary(tracks) == [
        ("00:00", "ID", "unidentified", False),
        ("06:00", "Mirage", "confirmed", False),
        ("12:30", "Inner Wars", "confirmed", False),
        ("17:00", "Bad Love", "confirmed", False),
        ("21:42", "Deixa Passar", "likely", True),
        ("26:00", "Corpo e Canção", "confirmed", False),
        ("31:30", "Stay", "confirmed", False),
        ("35:00", "Emotions", "confirmed", False),
        ("39:12", "Back To Life", "likely", True),
        ("43:51", "One Step At The Time", "likely", True),
        ("48:30", "ID", "likely", False),
        ("51:30", "Celebration", "confirmed", False),
        ("57:00", "Wish I Didn't Miss You", "confirmed", False),
    ]
    by_title = {t["title"]: t for t in tracks}
    # The description's labels win; the audio adds a version the description left out.
    assert by_title["Inner Wars"]["artist"] == "Antdot & Bakka (BR) & Miguel Ante ft. Rona Ray"
    assert by_title["Bad Love"]["version"] == "Antdot & Maz (BR) Remix"
    assert by_title["Corpo e Canção"]["version"] == "Club Mix"
    assert by_title["Celebration"]["version"] == "Antdot & Maz Edit"
    assert by_title["Wish I Didn't Miss You"]["artist"] == "Antdot & Mozambo"
    mirage = by_title["Mirage"]
    assert [(e["source"], e.get("hits")) for e in mirage["evidence"]] == [
        ("audio", 11),
        ("description", None),
    ]
    assert mirage["end"] == 750.0
    # Unheard songs sit between the heard songs around them.
    assert 1020 < by_title["Deixa Passar"]["start"] < 1560
    assert 2100 < by_title["Back To Life"]["start"] < by_title["One Step At The Time"]["start"]
    assert by_title["One Step At The Time"]["start"] < 3090
    # Two windows of an unlisted song in the 48:30 ID's stretch make it the likely ID.
    late_id = tracks[10]
    assert late_id["kind"] == "unknown" and late_id["artist"] is None
    top, other = late_id["candidates"]
    assert (top["artist"], top["title"], top["version"]) == (
        "Chris Fortier & Neil Kolo",
        "All I Got",
        "Chris Fortier 20yr Dub",
    )
    assert (top["hits"], top["at"], top["strength"]) == (2, 3030.0, "medium")
    assert (other["title"], other["strength"]) == ("128 MB of Netsex", "weak")
    # Songs heard in a single window never become tracks.
    titles = {t["title"] for t in tracks}
    assert not titles & (SINGLE_WINDOW | {"Back2back"})
    assert {u["title"] for u in keinemusik["unplaced"]} == {
        "Slacker",
        "Taj",
        "People Say (Tech House Edit)",
    }
    assert keinemusik["coverage"] == {
        "duration": 3720.0,
        "seconds_identified": 3180.0,
        "tracks": 13,
        "named": 11,
        "unknown": 2,
        "from_audio": 8,
        "from_text": 13,
        "gaps": [{"start": 0.0, "end": 360.0}, {"start": 2910.0, "end": 3090.0}],
    }
    audio, listed = keinemusik["sources"]
    assert (audio["windows"], audio["matched"], audio["errors"], audio["songs"]) == (124, 83, 0, 9)
    assert listed == {"source": "description", "entries": 13, "named": 11, "unknown": 2, "heard": 8}
    assert json.loads(json.dumps(keinemusik)) == keinemusik


def test_keinemusik_set_heard_without_a_tracklist(keinemusik_heard_only):
    tracks = keinemusik_heard_only["tracks"]
    assert [(clock(t["start"]), t["artist"], t["title"], t["status"]) for t in tracks] == [
        ("00:00", None, None, "unidentified"),
        ("06:00", "Antdot", "Mirage", "confirmed"),
        ("12:30", "Antdot, BAKKA (BR) & Miguel Ante", "Inner Wars (feat. Rona Ray)", "confirmed"),
        ("17:00", "Fahlberg", "Bad Love", "confirmed"),
        ("21:42", None, None, "unidentified"),
        ("26:00", "Antdot, Maz & Letícia Fialho", "Corpo e Canção", "confirmed"),
        ("31:30", "Antdot & Mozambo", "Stay", "confirmed"),
        ("35:00", "Maz, Antdot & Ayrton Senna", "Emotions", "confirmed"),
        ("39:12", None, None, "weak"),
        ("50:30", "Chris Fortier & Neil Kolo", "All I Got", "likely"),
        ("51:30", "Supermini & Frankie Romano", "Celebration", "confirmed"),
        ("57:00", "Mozambo & Antdot", "Wish I Didn't Miss You", "confirmed"),
    ]
    assert keinemusik_heard_only["coverage"]["gaps"] == [
        {"start": 0.0, "end": 360.0},
        {"start": 1302.0, "end": 1560.0},
        {"start": 2352.0, "end": 3030.0},
    ]
    stretch = tracks[8]
    assert (stretch["start"], stretch["end"], stretch["approx"]) == (2352.0, 3030.0, False)
    assert [c["title"] for c in stretch["candidates"]] == ["Slacker", "128 MB of Netsex"]
    assert {u["title"] for u in keinemusik_heard_only["unplaced"]} == {
        "Taj",
        "People Say (Tech House Edit)",
    }
    assert keinemusik_heard_only["coverage"]["from_text"] == 0


def test_keinemusik_request_items_validate(keinemusik, keinemusik_heard_only):
    items = request_items(keinemusik, SET_URL)
    request = valid_request(items)
    assert len(request.items) == 13
    first, mirage = items[:2]
    assert first == {
        "kind": "unknown",
        "label": "ID",
        "timestamp": "00:00",
        "source_url": SET_URL,
        "notes": "Listed as an ID in the description",
    }
    assert mirage["notes"] == "Shazam: 11 windows from 06:00; also in the description"
    assert items[4]["notes"] == "From the description; the time is approximate"
    late_id = items[10]
    assert late_id["label"] == "ID" and late_id["timestamp"] == "48:30"
    assert late_id["notes"].startswith(
        "ID (likely Chris Fortier & Neil Kolo - All I Got (Chris Fortier 20yr Dub)): "
        "Shazam heard it in 2 windows from 50:30"
    )
    assert items[11]["version"] == "Antdot & Maz Edit"
    heard_only = request_items(keinemusik_heard_only, SET_URL)
    valid_request(heard_only)
    assert heard_only[8]["notes"].startswith("Not identified from 39:12 to 50:30; possibly Q-Force")
    # An ID needs a time or a link to listen to.
    timeless = {"tracks": [{**keinemusik["tracks"][0], "start": None}]}
    assert request_items(timeless) == []


@pytest.mark.parametrize("which", ["keinemusik", "keinemusik_heard_only"])
def test_tracklist_text_reads_back_the_same(which, request):
    result = request.getfixturevalue(which)
    text = tracklist_text(result)
    assert text.splitlines()[:2] == ["[00:00] ID - ID", "[06:00] Antdot - Mirage"]
    items, skipped = parse_tracklist(text)
    assert skipped == [] and len(items) == len(result["tracks"])
    entries = listed_tracklist("file", text)["entries"]
    read_back = [
        (e["kind"], e["timestamp"], e["kind"] == "named" and display_label(e["artist"], e["title"]))
        for e in entries
    ]
    assert read_back == [
        (
            t["kind"],
            clock(t["start"]),
            t["kind"] == "named" and (t["artist"], t["title"], t["version"]),
        )
        for t in result["tracks"]
    ]
    # Read back as a listing, it lines up with the audio it came from.
    again = build_tracklist(
        duration=SET_LENGTH, probes=scan_probes(), listed=[listing("file", text)]
    )
    assert [t["title"] for t in again["tracks"]] == [t["title"] for t in result["tracks"]]


def test_listed_tracklist_keeps_the_times_on_named_lines():
    listed = listing(
        "1001tracklists",
        "Tracklist:",
        "01. [06:00] Antdot - Mirage",
        "02. Antdot & Mozambo - Stay @ 31:30",
        "w/ Maz - Emotions",
        "03. ID - ID [48:30]",
        "04. Supermini & Frankie Romano - Celebration",
    )
    assert listed["source"] == "1001tracklists"
    assert [(e["kind"], e["title"], e["timestamp"]) for e in listed["entries"]] == [
        ("named", "Mirage", "06:00"),
        ("named", "Stay", "31:30"),
        ("named", "Emotions", None),
        ("unknown", None, "48:30"),
        ("named", "Celebration", None),
    ]


def test_bridges_short_gaps_only():
    two_quiet = [
        *song(0, 3, "Lumen", "Halo"),
        silence(90),
        silence(120),
        *song(150, 2, "Lumen", "Halo"),
    ]
    (bridged,) = segments(two_quiet)
    assert (bridged["start"], bridged["end"], bridged["hits"]) == (0.0, 192.0, 5)
    three_quiet = [*song(0, 3, "Lumen", "Halo"), *map(silence, (90, 120, 150))]
    three_quiet += song(180, 2, "Lumen", "Halo")
    assert [s["hits"] for s in segments(three_quiet)] == [3, 2]
    # Errors are windows nobody heard; they still count towards the gap.
    failed = [{"at": 90.0, "match": None, "error": "timed out"}, silence(120)]
    assert (
        len(segments([*song(0, 3, "Lumen", "Halo"), *failed, *song(150, 1, "Lumen", "Halo")])) == 1
    )


def test_same_song_by_id_isrc_or_label():
    by_isrc = [
        heard(0, "Lumen", "Halo", isrc="gbxyz2600001"),
        heard(30, "Lumen & Ana", "Halo (Radio Edit)", isrc="GBXYZ2600001"),
        heard(60, "Someone Else", "Untitled", isrc="GBXYZ2600001"),
    ]
    (one,) = segments(by_isrc)
    assert one["strength"] == "strong" and one["ids"][0]["isrc"] == "GBXYZ2600001"
    by_label = [
        heard(0, "Ana feat. Bo", "Rain"),
        heard(30, "Ana", "Rain (feat. Bo) [Extended Mix]"),
        heard(60, "Bo & Ana", "Rain (Club Mix)"),
    ]
    assert len(segments(by_label)) == 1
    other_songs = [heard(0, "Ana", "Rain"), heard(30, "Ana", "Snow"), heard(60, "Bo", "Hail")]
    assert [s["title"] for s in segments(other_songs)] == ["Rain", "Snow", "Hail"]


def test_song_offsets_out_of_line_do_not_count():
    steady = [heard(at, "Lumen", "Halo", track_id="1", song_offset=at + 20) for at in (0, 30, 60)]
    jumped = heard(90, "Lumen", "Halo", track_id="1", song_offset=300)
    (halo,) = segments([*steady, jumped])
    assert (halo["hits"], halo["inconsistent"], halo["end"]) == (3, 1, 72.0)
    disagree = [
        heard(0, "Lumen", "Halo", song_offset=20),
        heard(30, "Lumen", "Halo", song_offset=200),
    ]
    assert segments(disagree)[0]["strength"] == "weak"
    # A track pitched up 5 % runs ahead of the set by its skew; that is still one play.
    pitched = [
        heard(at, "Lumen", "Halo", song_offset=20 + at * 1.05, skew=0.05)
        for at in range(0, 630, 30)
    ]
    assert segments(pitched)[0]["inconsistent"] == 0
    unskewed = [{**p, "match": {**p["match"], "skew": None}} for p in pitched]
    assert segments(unskewed)[0]["inconsistent"] > 0


def test_no_audio_keeps_the_listing_with_approximate_times():
    failed = [{"at": float(t), "match": None, "error": "blocked"} for t in range(0, 3720, 30)]
    for probes in ([], failed):
        result = build_tracklist(duration=SET_LENGTH, probes=probes, listed=[description()])
        tracks = result["tracks"]
        assert len(tracks) == 13
        assert [t["status"] for t in tracks if t["kind"] == "named"] == ["likely"] * 11
        assert [(clock(t["start"]), t["approx"]) for t in tracks if t["kind"] == "unknown"] == [
            ("00:00", False),
            ("48:30", False),
        ]
        starts = [t["start"] for t in tracks]
        assert starts == sorted(starts) and all(t["approx"] for t in tracks if t["kind"] == "named")
        assert result["coverage"]["from_audio"] == 0
        valid_request(request_items(result))
    errors = build_tracklist(duration=SET_LENGTH, probes=failed)
    assert errors["tracks"] == [] and errors["unplaced"] == []
    assert errors["coverage"]["gaps"] == [{"start": 0.0, "end": 3720.0}]
    assert errors["sources"][0]["errors"] == len(failed)
    nothing = build_tracklist()
    assert nothing["tracks"] == [] and nothing["sources"] == []
    assert nothing["coverage"]["duration"] is None and tracklist_text(nothing) == ""


def test_text_and_strong_audio_disagreeing_keeps_both():
    probes = [
        *song(0, 4, "Lumen", "Halo"),
        *song(120, 4, "Kettama", "Fall Into The Sky"),
        *song(240, 4, "Ana", "Rain"),
    ]
    listed = listing("description", "Lumen - Halo", "Bicep - Glue", "Ana - Rain")
    result = build_tracklist(duration=360, probes=probes, listed=[listed])
    assert summary(result["tracks"]) == [
        ("00:00", "Halo", "confirmed", False),
        ("02:00", "Glue", "conflict", True),
        ("02:00", "Fall Into The Sky", "conflict", False),
        ("04:00", "Rain", "confirmed", False),
    ]
    glue, sky = result["tracks"][1:3]
    assert glue["candidates"][0]["title"] == "Fall Into The Sky"
    assert sky["candidates"] == [
        {"source": "description", "artist": "Bicep", "title": "Glue", "version": ""}
    ]
    glue_item, sky_item = request_items(result)[1:3]
    assert glue_item["notes"] == (
        "From the description; but Shazam heard Kettama - Fall Into The Sky here "
        "(4 windows from 02:00); the time is approximate"
    )
    assert (
        sky_item["notes"]
        == "Shazam: 4 windows from 02:00; but the description lists Bicep - Glue here"
    )
    # Two windows are not enough to dispute a listing: the song is added beside it.
    probes[4:8] = probes[4:6]
    softer = build_tracklist(duration=360, probes=probes, listed=[listed])
    assert summary(softer["tracks"])[1:3] == [
        ("01:42", "Glue", "likely", True),
        ("02:00", "Fall Into The Sky", "likely", False),
    ]


def test_text_out_of_order_follows_the_audio():
    probes = [
        *song(0, 4, "Lumen", "Halo"),
        *song(120, 4, "Ana", "Rain"),
        *song(240, 4, "Bo", "Snow"),
    ]
    listed = listing("description", "Bo - Snow", "Lumen - Halo", "Ana - Rain")
    tracks = build_tracklist(duration=360, probes=probes, listed=[listed])["tracks"]
    assert summary(tracks) == [
        ("00:00", "Halo", "confirmed", False),
        ("02:00", "Rain", "confirmed", False),
        ("04:00", "Snow", "confirmed", False),
    ]
    assert tracks[2]["evidence"][1] == {
        "source": "description",
        "kind": "named",
        "position": 1,
        "label": "Bo - Snow",
        "timestamp": None,
        "moved": True,
    }


def test_two_listings_confirm_each_other_and_fill_ids():
    description_ = listing("description", "Lumen - Halo", "[02:00] ID - ID", "Ana - Rain")
    tracklists = listing(
        "1001tracklists", "Lumen - Halo (Extended Mix)", "[02:10] Bicep - Glue", "Ana - Rain"
    )
    result = build_tracklist(duration=360, listed=[description_, tracklists])
    tracks = result["tracks"]
    assert [(t["title"], t["version"], t["status"]) for t in tracks] == [
        ("Halo", "", "confirmed"),
        ("Glue", "", "likely"),
        ("Rain", "", "confirmed"),
    ]
    assert [(e["source"], e["kind"]) for e in tracks[1]["evidence"]] == [
        ("1001tracklists", "named"),
        ("description", "unknown"),
    ]
    assert (tracks[1]["start"], tracks[1]["approx"]) == (130.0, False)
    assert request_items(result)[1]["notes"] == "From 1001Tracklists; an ID in the description"
    # An ID both listings leave open stays one ID.
    both = listing("1001tracklists", "Lumen - Halo", "ID - ID @ 2:05", "Ana - Rain")
    tracks = build_tracklist(duration=360, listed=[description_, both])["tracks"]
    assert [t["kind"] for t in tracks] == ["named", "unknown", "named"]
    assert len(tracks[1]["evidence"]) == 2


def test_comments_suggest_ids_but_not_songs_already_listed():
    comments = [
        {"text": "ID at 48:40 is Chris Fortier & Neil Kolo - All I Got", "like_count": 3},
        {"text": "48:45 Chris Fortier & Neil Kolo - All I Got 🔥", "like_count": 1},
        {"text": "0:30 Antdot - Mirage", "like_count": 9},
        {"text": "1:10 Keinemusik - Intro Edit", "like_count": 5},
    ]
    result = build_tracklist(
        duration=SET_LENGTH, probes=scan_probes(), listed=[description()], comments=comments
    )
    opening, late_id = (t for t in result["tracks"] if t["kind"] == "unknown")
    assert [(c["source"], c["title"]) for c in opening["candidates"]] == [
        ("comments", "Intro Edit")
    ]
    assert opening["status"] == "weak"
    assert [(c["source"], c["title"]) for c in late_id["candidates"]] == [
        ("audio", "All I Got"),
        ("audio", "128 MB of Netsex"),
        ("comments", "All I Got"),
    ]
    assert late_id["candidates"][2]["mentions"] == 2
    assert result["sources"][-1] == {"source": "comments", "comments": 4, "suggestions": 2}
    notes = request_items(result, SET_URL)[0]["notes"]
    assert notes == "Listed as an ID in the description; listeners named Keinemusik - Intro Edit"
