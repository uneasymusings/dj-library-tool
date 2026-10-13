"""One tracklist from audio recognition, published tracklists and comments.

The regression data is a real scan of a 62-minute Keinemusik set: Shazam on a 12 s window
every 30 s (tests/data/keinemusik-scan.jsonl) and the set's published description. A later
live run of `djlib identify` on the same show (keinemusik-live-probes.jsonl: 124 windows, then
33 more where songs change) comes with the upload's whole description.
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
from djlib.application.source_matching import tracklist_from_source
from djlib.application.tracklists import parse_tracklist
from djlib.domain.request_contracts import RequestCreate

DATA = Path(__file__).parent / "data"
SET_LENGTH = 62 * 60
SET_URL = "https://www.youtube.com/watch?v=keinemusik"
SINGLE_WINDOW = {"Slacker", "128 MB of Netsex", "Taj", "People Say (Tech House Edit)"}
LIVE_LENGTH = 3729.121
LIVE_URL = "https://www.youtube.com/watch?v=cmgN8X7Pp_0"


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


def failed(at: float) -> dict:
    return {"at": float(at), "window": 12.0, "match": None, "error": "Shazam rate limit (429)"}


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


def live_probes() -> list[dict]:
    rows = (DATA / "keinemusik-live-probes.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(row) for row in rows]


def live_listing() -> dict:
    """The upload's description as the identification reads it."""
    text = (DATA / "keinemusik-live-description.txt").read_text(encoding="utf-8")
    return listed_tracklist("description", tracklist_from_source(text, []), LIVE_URL)


def live_run(listed: list[dict]) -> dict:
    return build_tracklist(duration=LIVE_LENGTH, probes=live_probes(), listed=listed, stride=30)


def test_a_second_listen_between_windows_never_counts_as_a_window():
    """recognition.refine listens between windows where songs change or a window failed."""
    probes = [
        *song(0, 3, "Lumen", "Halo"),
        heard(75, "Q-Force", "Slacker"),  # checking the change before it: not a second window
        heard(90, "Q-Force", "Slacker"),
        heard(105, "Q-Force", "Slacker"),  # nor after it
        failed(120),
        heard(135, "Nia Okoro", "Slow Burn"),  # fills the failed window and places the change
        *song(150, 4, "Nia Okoro", "Slow Burn"),
        heard(255, "Bicep", "Glue"),  # a song only a check between windows heard
        *song(270, 2, "Lumen", "Halo"),
    ]
    found = segments(probes, stride=30)
    assert [(s["title"], s["hits"], s["strength"], s["start"]) for s in found] == [
        ("Halo", 3, "strong", 0.0),
        ("Slacker", 1, "weak", 75.0),
        ("Slow Burn", 5, "strong", 135.0),
        ("Glue", 1, "weak", 255.0),
        ("Halo", 2, "medium", 270.0),
    ]
    assert found[2]["times"] == [135.0, 150.0, 180.0, 210.0, 240.0]
    tracks = build_tracklist(probes=probes, stride=30)["tracks"]
    assert [t["title"] for t in tracks] == ["Halo", "Slow Burn", "Halo"]


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
    # Named is not heard: nothing was heard of the opening ID, Deixa Passar, or from Back To
    # Life until the 48:30 ID's likely song.
    assert keinemusik["coverage"] == {
        "duration": 3720.0,
        "seconds_identified": 3180.0,
        "seconds_heard": 2238.0,
        "tracks": 13,
        "named": 11,
        "unknown": 2,
        "from_audio": 8,
        "from_text": 13,
        "gaps": [
            {"start": 0.0, "end": 360.0},
            {"start": 1302.0, "end": 1560.0},
            {"start": 2352.0, "end": 3030.0},
        ],
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


def test_keinemusik_live_run_keeps_to_the_description():
    """The live run listed 17 tracks for the description's 13: four "IDs" where Shazam could
    not lock on, between two listed songs or after the last one."""
    result = live_run([live_listing()])
    tracks = result["tracks"]
    # Shazam heard Inner Wars from 12:54, Wish I Didn't Miss You from 56:54 (and its sample,
    # Angie Stone's original), with song offsets out of line; the live run started them at
    # 13:54 and 58:09.
    assert summary(tracks) == [
        ("00:00", "ID", "unidentified", False),
        ("05:39", "Mirage", "confirmed", False),
        ("12:54", "Inner Wars", "confirmed", False),
        ("16:54", "Bad Love", "confirmed", False),
        ("21:40", "Deixa Passar", "likely", False),
        ("26:09", "Corpo e Canção", "confirmed", False),
        ("31:39", "Stay", "confirmed", False),
        ("34:39", "Emotions", "confirmed", False),
        ("39:00", "Back To Life", "likely", False),
        ("43:40", "One Step At The Time", "likely", False),
        ("48:30", "ID", "weak", False),
        ("51:24", "Celebration", "confirmed", False),
        ("56:54", "Wish I Didn't Miss You", "confirmed", False),
    ]
    late_id = tracks[10]
    assert [(c["artist"], c["title"], clock(c["at"])) for c in late_id["candidates"]] == [
        ("Chris Fortier & Neil Kolo", "All I Got", "50:09"),
        ("BR1S4", "THIS IS BRAZIL", "51:09"),
    ]
    # Songs heard once between Celebration and Wish I Didn't Miss You are no ID of their own.
    assert [(u["title"], clock(u["at"])) for u in result["unplaced"]] == [
        ("U Sure Do", "55:39"),
        ("New Millenium (feat. Topazz)", "55:54"),
        ("Salva Mea", "56:09"),
        ("All I Got", "56:24"),
        ("THIS IS BRAZIL", "56:39"),
    ]
    # Named is not heard: the opening ID, Deixa Passar, and Back To Life to Celebration.
    coverage = result["coverage"]
    assert coverage["gaps"] == [
        {"start": 0.0, "end": 339.0},
        {"start": 1296.0, "end": 1569.0},
        {"start": 2376.0, "end": 3084.0},
    ]
    assert (round(coverage["seconds_identified"]), coverage["seconds_heard"]) == (3216, 2301.0)
    assert (coverage["named"], coverage["unknown"], coverage["from_audio"]) == (11, 2, 8)
    items = request_items(result, LIVE_URL)
    assert len(valid_request(items).items) == 13
    assert [item["kind"] for item in items].count("unknown") == 2
    text = tracklist_text(result)
    assert text.splitlines()[-1] == "[56:54] Antdot & Mozambo - Wish I Didn't Miss You"
    assert [t["title"] for t in live_run([listing("file", text)])["tracks"]] == [
        t["title"] for t in tracks
    ]


def test_keinemusik_live_stretches_nobody_heard_stay_with_the_song_before():
    """The live run's four "IDs", as if Shazam had heard nothing of the songs there."""
    quiet = [(711, 834), (921, 1014), (3291, 3489), (3591, LIVE_LENGTH)]
    songs = ("Mirage", "Inner Wars", "Celebration", "Wish I Didn't Miss You")
    probes = []
    for probe in live_probes():
        match = probe["match"]
        if match and any(a <= probe["at"] < b for a, b in quiet):
            match = None if any(title in match["title"] for title in songs) else match
        probes.append({**probe, "match": match})
    result = build_tracklist(
        duration=LIVE_LENGTH, probes=probes, listed=[live_listing()], stride=30
    )
    tracks = result["tracks"]
    assert [(clock(t["start"]), t["title"] or "ID") for t in tracks] == [
        ("00:00", "ID"),
        ("05:39", "Mirage"),
        ("13:54", "Inner Wars"),
        ("16:54", "Bad Love"),
        ("21:40", "Deixa Passar"),
        ("26:09", "Corpo e Canção"),
        ("31:39", "Stay"),
        ("34:39", "Emotions"),
        ("39:00", "Back To Life"),
        ("43:40", "One Step At The Time"),
        ("48:30", "ID"),
        ("51:24", "Celebration"),
        ("58:09", "Wish I Didn't Miss You"),
    ]
    assert (tracks[-1]["end"], tracks[10]["candidates"][0]["title"]) == (LIVE_LENGTH, "All I Got")
    # Still not heard, and said so.
    assert [(clock(g["start"]), clock(g["end"])) for g in result["coverage"]["gaps"]] == [
        ("00:00", "05:39"),
        ("11:51", "13:54"),
        ("15:21", "16:54"),
        ("21:36", "26:09"),
        ("39:36", "51:24"),
        ("54:51", "58:09"),
        ("59:51", "1:02:09"),
    ]


def test_keinemusik_live_run_heard_without_a_tracklist():
    """Only the long stretches nobody heard are IDs: the opening, Deixa Passar, and Back To
    Life to Celebration (two songs and the 48:30 ID)."""
    result = live_run([])
    assert [(clock(t["start"]), t["title"] or "ID", t["status"]) for t in result["tracks"]] == [
        ("00:00", "ID", "unidentified"),
        ("05:39", "Mirage", "confirmed"),
        ("12:54", "Inner Wars (feat. Rona Ray)", "confirmed"),
        ("16:54", "Bad Love", "confirmed"),
        ("21:36", "ID", "unidentified"),
        ("26:09", "Corpo e Canção", "confirmed"),
        ("31:39", "Stay", "confirmed"),
        ("34:39", "Emotions", "confirmed"),
        ("39:36", "ID", "weak"),
        ("51:24", "Celebration", "confirmed"),
        ("56:54", "Wish I Didn't Miss You", "confirmed"),
    ]
    assert result["coverage"]["gaps"] == [
        {"start": 0.0, "end": 339.0},
        {"start": 1296.0, "end": 1569.0},
        {"start": 2376.0, "end": 3084.0},
    ]
    long_id = result["tracks"][8]
    assert [c["title"] for c in long_id["candidates"]] == ["All I Got", "THIS IS BRAZIL"]
    assert len(valid_request(request_items(result, LIVE_URL)).items) == 11


def test_listed_tracklist_keeps_the_times_on_named_lines():
    listed = listing(
        "1001tracklists",
        "Tracklist:",
        "01. [06:00] Antdot - Mirage",
        "02. Antdot & Mozambo - Stay @ 31:30",
        "w/ Maz - Emotions",
        "03. ID - ID [48:30]",
        "04. Supermini & Frankie Romano - Celebration",
        "Thanks for listening",
    )
    assert listed["source"] == "1001tracklists"
    assert [(e["kind"], e["title"], e["timestamp"]) for e in listed["entries"]] == [
        ("named", "Mirage", "06:00"),
        ("named", "Stay", "31:30"),
        ("named", "Emotions", None),
        ("unknown", None, "48:30"),
        ("named", "Celebration", None),
    ]
    # Lines left out are reported, so the caller can say which ones were not read.
    assert listed["skipped"] == [
        {"line": 7, "text": "Thanks for listening", "reason": "no “Artist - Title” separator"}
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
    # A window where recognition failed (rate limited) heard nothing else, so it is no gap;
    # only the time limit applies.
    limited = [*song(0, 3, "Lumen", "Halo"), *map(failed, (90, 120, 150))]
    (bridged,) = segments([*limited, *song(180, 2, "Lumen", "Halo")])
    assert (bridged["hits"], bridged["end"]) == (5, 222.0)
    long_outage = [*song(0, 3, "Lumen", "Halo"), *map(failed, range(90, 360, 30))]
    assert len(segments([*long_outage, *song(360, 2, "Lumen", "Halo")])) == 2
    # Probes missing from the list still count as heard windows, one per stride.
    sparse = [*song(0, 3, "Lumen", "Halo"), *song(180, 2, "Lumen", "Halo")]
    assert [s["hits"] for s in segments(sparse)] == [3, 2]


def test_probes_at_the_same_time_are_one_window():
    def acrcloud(at: float, artist: str | None = None, title: str | None = None) -> dict:
        match = {"provider": "acrcloud", "artist": artist, "title": title} if artist else None
        return {"at": float(at), "window": 12.0, "match": match, "error": None}

    both = []
    for at in (0, 30, 60, 150, 180):
        both += [heard(at, "Lumen", "Halo"), acrcloud(at, "Lumen", "Halo")]
    for at in (90, 120):
        both += [silence(at), acrcloud(at)]
    (halo,) = segments(both)
    assert halo["hits"] == 5
    assert [(p["provider"], p["hits"]) for p in halo["providers"]] == [
        ("shazam", 5),
        ("acrcloud", 5),
    ]
    # A failed attempt recorded next to its retry is the same window.
    retried = []
    for at in range(0, 300, 30):
        retried += [failed(at), heard(at, "Lumen", "Halo") if at != 90 else silence(at)]
    assert [s["hits"] for s in segments(retried)] == [9]


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


def test_a_longer_title_is_the_same_song_only_when_it_adds_credited_names():
    love = [*song(0, 6, "Antdot", "Love"), *song(180, 6, "Antdot", "Bad Love")]
    assert [s["title"] for s in segments(love)] == ["Love", "Bad Love"]
    home = [*song(0, 4, "Antdot", "Home"), *song(120, 4, "Maz", "Take Me Home (Antdot Remix)")]
    assert [s["title"] for s in segments(home)] == ["Home", "Take Me Home"]
    # A remix credit is not the listed song, so no version is borrowed from it.
    probes = [
        *song(0, 6, "Lumen", "Halo"),
        *song(180, 6, "Fahlberg", "Bad Love (Antdot Remix)"),
        *song(360, 6, "Ana", "Rain"),
    ]
    listed = listing("description", "Lumen - Halo", "Antdot - Love", "Ana - Rain")
    tracks = build_tracklist(duration=600, probes=probes, listed=[listed])["tracks"]
    assert [(t["title"], t["version"], t["status"]) for t in tracks] == [
        ("Halo", "", "confirmed"),
        ("Love", "", "conflict"),
        ("Bad Love", "Antdot Remix", "conflict"),
        ("Rain", "", "confirmed"),
    ]


def test_control_characters_in_heard_labels_are_cleaned():
    probes = [*song(0, 4, "Lumen", "Halo\tExtended"), *song(120, 4, "Ana\n", "Rain\x00")]
    result = build_tracklist(duration=240, probes=probes)
    assert [(t["artist"], t["title"]) for t in result["tracks"]] == [
        ("Lumen", "Halo Extended"),
        ("Ana", "Rain"),
    ]
    valid_request(request_items(result))
    assert segments([heard(0, "\x00", "Halo"), heard(30, "Lumen", "\x07")]) == []


def test_song_offsets_out_of_line_do_not_count():
    steady = [heard(at, "Lumen", "Halo", track_id="1", song_offset=at + 20) for at in (0, 30, 60)]
    jumped = heard(90, "Lumen", "Halo", track_id="1", song_offset=300)
    (halo,) = segments([*steady, jumped])
    # The song was still playing there (a loop, a repeated part): it counts for the time only.
    assert (halo["hits"], halo["inconsistent"], halo["end"]) == (3, 1, 102.0)
    assert halo["times"] == [0.0, 30.0, 60.0]
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
    blocked = [failed(t) for t in range(0, 3720, 30)]
    for probes in ([], blocked):
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
    # Nothing heard and nothing listed: the whole set is one ID.
    errors = build_tracklist(duration=SET_LENGTH, probes=blocked)
    (whole,) = errors["tracks"]
    assert (whole["kind"], whole["start"], whole["end"], whole["status"]) == (
        "unknown",
        0.0,
        3720.0,
        "unidentified",
    )
    assert errors["coverage"]["gaps"] == [{"start": 0.0, "end": 3720.0}]
    assert errors["coverage"]["unknown"] == 1 and errors["unplaced"] == []
    assert errors["sources"][0]["errors"] == len(blocked)
    # Without a duration, the last window says how long the set is.
    assert build_tracklist(probes=blocked)["tracks"][0]["end"] == 3702.0
    nothing = build_tracklist()
    assert nothing["tracks"] == [] and nothing["sources"] == []
    assert nothing["coverage"]["duration"] is None and tracklist_text(nothing) == ""


def test_a_long_stretch_nobody_heard_is_an_id_only_without_a_listing():
    probes = [
        *song(0, 6, "Lumen", "Halo"),
        *map(silence, range(180, 1380, 30)),
        *song(1380, 6, "Ana", "Rain"),
    ]
    probes[20] = heard(600, "Bo", "Snow")
    result = build_tracklist(duration=1600, probes=probes)
    tracks = result["tracks"]
    assert [(t["start"], t["end"], t["title"]) for t in tracks] == [
        (0.0, 162.0, "Halo"),
        (162.0, 1380.0, None),
        (1380.0, 1600.0, "Rain"),
    ]
    assert [c["title"] for c in tracks[1]["candidates"]] == ["Snow"]
    coverage = result["coverage"]
    assert coverage["gaps"] == [{"start": 162.0, "end": 1380.0}]
    assert (coverage["seconds_identified"], coverage["seconds_heard"]) == (382.0, 324.0)
    assert coverage["unknown"] == 1
    # The listing says Rain follows Halo, so the stretch is Halo's, and still not heard. A song
    # heard once there fits no listed ID.
    listed = listing("description", "Lumen - Halo", "Ana - Rain")
    result = build_tracklist(duration=1600, probes=probes, listed=[listed])
    assert [(t["start"], t["end"], t["title"]) for t in result["tracks"]] == [
        (0.0, 1380.0, "Halo"),
        (1380.0, 1600.0, "Rain"),
    ]
    assert [u["title"] for u in result["unplaced"]] == ["Snow"]
    coverage = result["coverage"]
    assert coverage["gaps"] == [{"start": 162.0, "end": 1380.0}]
    assert (coverage["seconds_identified"], coverage["seconds_heard"]) == (1600.0, 324.0)
    assert coverage["unknown"] == 0


def test_without_a_listing_a_short_stretch_is_the_song_before_ending():
    def heard_with_quiet(windows: int) -> list[dict]:
        quiet = 180 + 30 * windows
        return [
            *song(0, 6, "Lumen", "Halo"),
            *map(silence, range(180, quiet, 30)),
            *song(quiet, 6, "Ana", "Rain"),
        ]

    # Five windows (2:48) where nothing was heard: Halo's breakdown or outro, not a song.
    result = build_tracklist(probes=heard_with_quiet(5))
    assert summary(result["tracks"]) == [
        ("00:00", "Halo", "confirmed", False),
        ("05:30", "Rain", "confirmed", False),
    ]
    coverage = result["coverage"]
    assert coverage["gaps"] == [{"start": 162.0, "end": 330.0}]
    assert (coverage["seconds_identified"], coverage["seconds_heard"]) == (492.0, 324.0)
    # Six (3:18) is long enough to be a song nobody recognized.
    result = build_tracklist(probes=heard_with_quiet(6))
    assert summary(result["tracks"])[1] == ("02:42", "ID", "unidentified", False)
    # Nor is it a song when Halo is heard again after it: Halo was still playing.
    again = [*song(0, 6, "Lumen", "Halo"), *map(silence, range(180, 600, 30))]
    again += [*song(600, 4, "Lumen", "Halo"), *song(720, 6, "Ana", "Rain")]
    result = build_tracklist(probes=again)
    assert summary(result["tracks"]) == [
        ("00:00", "Halo", "confirmed", False),
        ("12:00", "Rain", "confirmed", False),
    ]
    assert [e["at"] for e in result["tracks"][0]["evidence"]] == [0.0, 600.0]
    assert result["coverage"]["gaps"] == [{"start": 162.0, "end": 600.0}]


def test_with_a_listing_only_an_opening_it_leaves_out_is_an_id():
    probes = [*map(silence, range(0, 300, 30)), *song(300, 6, "Lumen", "Halo")]
    probes += song(480, 6, "Ana", "Rain")
    untimed = listing("description", "Lumen - Halo", "Ana - Rain")
    result = build_tracklist(duration=660, probes=probes, listed=[untimed])
    assert summary(result["tracks"]) == [
        ("00:00", "ID", "unidentified", False),
        ("05:00", "Halo", "confirmed", False),
        ("08:00", "Rain", "confirmed", False),
    ]
    # A listing that starts at 00:00 says the first song is the opening.
    timed = listing("description", "[00:00] Lumen - Halo", "Ana - Rain")
    result = build_tracklist(duration=660, probes=probes, listed=[timed])
    assert [t["title"] for t in result["tracks"]] == ["Halo", "Rain"]
    assert result["coverage"]["gaps"] == [{"start": 0.0, "end": 300.0}]
    # So does a first song heard less than three minutes in.
    sooner = [*map(silence, range(0, 150, 30)), *song(150, 6, "Lumen", "Halo")]
    sooner += song(330, 6, "Ana", "Rain")
    result = build_tracklist(duration=650, probes=sooner, listed=[untimed])
    assert [t["title"] for t in result["tracks"]] == ["Halo", "Rain"]
    assert result["tracks"][0]["start"] == 150.0


def test_a_song_heard_once_that_names_a_neighbour_is_that_song():
    """A sample or another version of the track before or after it is no other song."""
    probes = [
        *song(0, 6, "Lumen", "Halo"),
        *map(silence, (180, 210, 240, 270)),
        heard(300, "Angie Stone", "Wish I Didn't Miss You"),
        *map(silence, (330, 360, 390, 420)),
        *song(450, 6, "Mozambo & Antdot", "Wish I Didn't Miss You"),
    ]
    probes += map(silence, range(630, 1200, 30))
    for tracklists in ([listing("description", "Lumen - Halo", "[07:30] ID - ID")], []):
        result = build_tracklist(duration=1200, probes=probes, listed=tracklists)
        candidates = [c["artist"] for t in result["tracks"] for c in t["candidates"]]
        assert "Angie Stone" not in candidates and result["unplaced"] == []
    # Another song heard once there is still a candidate for the ID nothing was heard in.
    probes[10] = heard(300, "Bo", "Snow")
    result = build_tracklist(duration=1200, probes=probes)
    assert summary(result["tracks"])[1] == ("02:42", "ID", "weak", False)
    assert [c["title"] for c in result["tracks"][1]["candidates"]] == ["Snow"]


def test_single_window_songs_alone_make_one_id():
    probes = [silence(t) for t in range(0, 1800, 30)]
    probes[10], probes[30] = heard(300, "Lumen", "Halo"), heard(900, "Ana", "Rain")
    result = build_tracklist(duration=1800, probes=probes)
    (whole,) = result["tracks"]
    assert (whole["kind"], whole["start"], whole["end"], whole["status"]) == (
        "unknown",
        0.0,
        1800.0,
        "weak",
    )
    assert [c["title"] for c in whole["candidates"]] == ["Halo", "Rain"]
    assert result["unplaced"] == [] and result["coverage"]["unknown"] == 1
    (item,) = valid_request(request_items(result, SET_URL)).items
    assert item.notes == (
        "Not identified from 00:00 to 30:00; possibly Lumen - Halo (Shazam, 1 window at 05:00); "
        "possibly Ana - Rain (Shazam, 1 window at 15:00)"
    )
    assert tracklist_text(result) == "[00:00] ID - ID"


def test_each_listed_id_takes_the_song_heard_nearest_it():
    probes = [
        *song(0, 4, "Lumen", "Halo"),
        *song(150, 4, "Kettama", "Sky"),
        *map(silence, range(270, 600, 30)),
        *song(600, 2, "Bo", "Snow"),
        *song(660, 4, "Ana", "Rain"),
    ]
    listed = listing("description", "Lumen - Halo", "[10:00] ID - ID", "Ana - Rain")
    result = build_tracklist(duration=800, probes=probes, listed=[listed])
    tracks = result["tracks"]
    # The listing goes from Halo to the 10:00 ID: nothing heard from 04:12 is no other ID.
    assert summary(tracks) == [
        ("00:00", "Halo", "confirmed", False),
        ("02:30", "Sky", "confirmed", False),
        ("10:00", "ID", "likely", False),
        ("11:00", "Rain", "confirmed", False),
    ]
    assert [c["title"] for c in tracks[2]["candidates"]] == ["Snow"]
    assert result["coverage"]["gaps"] == [{"start": 252.0, "end": 600.0}]
    # IDs listed without a time take the songs heard in order, and their times.
    probes = [
        *song(0, 4, "Lumen", "Halo"),
        *song(180, 2, "Kettama", "Sky"),
        *map(silence, (240, 270, 300, 330)),
        *song(360, 3, "Bo", "Snow"),
        *map(silence, (450, 480, 510)),
        *song(540, 4, "Ana", "Rain"),
    ]
    lines = ["Lumen - Halo", "ID - ID", "ID - ID", "Ana - Rain"]
    listed = listed_tracklist("description", "\n".join(lines), SET_URL)
    tracks = build_tracklist(duration=660, probes=probes, listed=[listed])["tracks"]
    assert summary(tracks) == [
        ("00:00", "Halo", "confirmed", False),
        ("03:00", "ID", "likely", False),
        ("06:00", "ID", "likely", False),
        ("09:00", "Rain", "confirmed", False),
    ]
    assert [t["candidates"][0]["title"] for t in tracks[1:3]] == ["Sky", "Snow"]


def test_an_ids_songs_heard_once_while_it_plays_come_first():
    probes = [*song(0, 4, "Lumen", "Halo"), *map(silence, range(120, 900, 30))]
    probes += song(900, 4, "Ana", "Rain")
    probes[6], probes[21] = heard(180, "Bo", "Snow"), heard(630, "Kettama", "Sky")
    listed = listing("description", "Lumen - Halo", "[10:00] ID - ID", "Ana - Rain")
    result = build_tracklist(duration=1020, probes=probes, listed=[listed])
    late_id = result["tracks"][1]
    assert (clock(late_id["start"]), late_id["status"]) == ("10:00", "weak")
    assert [(c["title"], clock(c["at"])) for c in late_id["candidates"]] == [
        ("Sky", "10:30"),
        ("Snow", "03:00"),
    ]


def test_a_song_heard_far_from_the_listed_id_stays_a_track():
    # Q-Force's Slacker, heard once at 40:00, now heard for three windows.
    probes = scan_probes()
    slacker = next(p["match"] for p in probes if p["match"] and p["match"]["artist"] == "Q-Force")
    for probe in probes:
        if probe["at"] in (2430.0, 2460.0):
            probe["match"] = slacker
    text = (DATA / "keinemusik-description.txt").read_text(encoding="utf-8")
    shorter = "\n".join(line for line in text.splitlines() if "Back To Life" not in line)
    shorter = "\n".join(line for line in shorter.splitlines() if "One Step" not in line)
    result = build_tracklist(
        duration=SET_LENGTH, probes=probes, listed=[listing("description", shorter)]
    )
    # Nothing heard from 41:12 is still Slacker: the listing's next song is the 48:30 ID.
    assert summary(result["tracks"])[7:] == [
        ("35:00", "Emotions", "confirmed", False),
        ("40:00", "Slacker", "confirmed", False),
        ("48:30", "ID", "likely", False),
        ("51:30", "Celebration", "confirmed", False),
        ("57:00", "Wish I Didn't Miss You", "confirmed", False),
    ]
    late_id = result["tracks"][9]
    assert [(c["title"], c["strength"]) for c in late_id["candidates"]] == [
        ("All I Got", "medium"),
        ("128 MB of Netsex", "weak"),
    ]


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


def test_a_song_heard_again_soon_after_is_one_track():
    stray = [
        *song(0, 5, "Lumen", "Halo"),
        *song(180, 6, "Ana", "Rain"),
        *map(silence, (360, 390, 420)),
        heard(450, "Ana", "Rain"),
        *map(silence, (480, 510)),
        *song(540, 5, "Bo", "Snow"),
    ]
    again = [*stray[:11], *map(silence, (360, 390, 420, 450)), *song(480, 2, "Ana", "Rain")]
    again += stray[-5:]
    listed = listing("description", "Lumen - Halo", "Ana - Rain", "Bo - Snow")
    for probes in (stray, again):
        assert [s["title"] for s in segments(probes)] == ["Halo", "Rain", "Rain", "Snow"]
        for tracklists in ([listed], []):
            result = build_tracklist(duration=720, probes=probes, listed=tracklists)
            assert [(clock(t["start"]), t["title"]) for t in result["tracks"]] == [
                ("00:00", "Halo"),
                ("03:00", "Rain"),
                ("09:00", "Snow"),
            ]
    rain = build_tracklist(duration=720, probes=stray, listed=[listed])["tracks"][1]
    assert [(e["source"], e.get("hits")) for e in rain["evidence"]] == [
        ("audio", 7),
        ("description", None),
    ]
    # Past another song, a listing that names it once says it is still the same play.
    back = [
        *song(0, 4, "Lumen", "Halo"),
        *song(120, 4, "Ana", "Rain"),
        *song(240, 3, "Kettama", "Sky"),
        *song(330, 3, "Ana", "Rain"),
        *song(450, 4, "Bo", "Snow"),
    ]
    listed = listing("description", "Lumen - Halo", "Ana - Rain", "Bo - Snow")
    tracks = build_tracklist(duration=570, probes=back, listed=[listed])["tracks"]
    assert summary(tracks) == [
        ("00:00", "Halo", "confirmed", False),
        ("02:00", "Rain", "confirmed", False),
        ("04:00", "Sky", "confirmed", False),
        ("07:30", "Snow", "confirmed", False),
    ]
    assert request_items({"tracks": tracks})[1]["notes"] == (
        "Shazam: 4 windows from 02:00; Shazam: 3 windows from 05:30; also in the description"
    )
    unlisted = build_tracklist(duration=570, probes=back)["tracks"]
    assert [t["title"] for t in unlisted] == ["Halo", "Rain", "Sky", "Rain", "Snow"]
    # The listed song takes the windows at its listed time, else the ones heard longest.
    twice = [
        *song(0, 2, "Lumen", "Halo"),
        *song(60, 4, "Ana", "Rain"),
        *song(180, 3, "Lumen", "Halo"),
    ]
    for line, order in (
        ("[00:00] Lumen - Halo", [("00:00", "Halo"), ("01:00", "Rain")]),
        ("Lumen - Halo", [("01:00", "Rain"), ("03:00", "Halo")]),
    ):
        tracks = build_tracklist(duration=300, probes=twice, listed=[listing("file", line)])
        assert [(clock(t["start"]), t["title"]) for t in tracks["tracks"]] == order
        (halo,) = (t for t in tracks["tracks"] if t["title"] == "Halo")
        assert sorted(e.get("hits") or 0 for e in halo["evidence"]) == [0, 2, 3]
    # Without a listing, a song heard again after another one was played twice.
    tracks = build_tracklist(duration=300, probes=twice)["tracks"]
    assert [t["title"] for t in tracks] == ["Halo", "Rain", "Halo"]


def test_two_listings_fill_ids_but_confirm_nothing_unheard():
    description_ = listing("description", "Lumen - Halo", "[02:00] ID - ID", "Ana - Rain")
    tracklists = listing(
        "1001tracklists", "Lumen - Halo (Extended Mix)", "[02:10] Bicep - Glue", "Ana - Rain"
    )
    result = build_tracklist(duration=360, listed=[description_, tracklists])
    tracks = result["tracks"]
    assert [(t["title"], t["version"], t["status"]) for t in tracks] == [
        ("Halo", "", "likely"),
        ("Glue", "", "likely"),
        ("Rain", "", "likely"),
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


def test_only_the_audio_confirms_a_listed_song():
    # MixesDB pages often copy the upload's description, so two listings are one source.
    text = ("Lumen - Halo", "Ana - Rain", "Bo - Snow")
    listed = [listing("description", *text), listing("mixesdb", *text)]
    probes = [
        *song(0, 4, "Lumen", "Halo"),
        *map(silence, range(120, 300, 30)),
        heard(300, "Bo", "Snow"),
        *map(silence, (330, 360)),
    ]
    result = build_tracklist(duration=390, probes=probes, listed=listed)
    assert summary(result["tracks"]) == [
        ("00:00", "Halo", "confirmed", False),
        ("01:42", "Rain", "likely", True),
        ("05:00", "Snow", "confirmed", False),  # one window where the listings place it
    ]
    assert [item["notes"] for item in request_items(result)] == [
        "Shazam: 4 windows from 00:00; also in the description and MixesDB",
        "From the description and MixesDB; the time is approximate",
        "Shazam: 1 window at 05:00; also in the description and MixesDB",
    ]
    # Heard in three or more windows confirms a song no listing names.
    alone = build_tracklist(duration=390, probes=probes)["tracks"]
    assert [(t["title"], t["status"]) for t in alone if t["kind"] == "named"] == [
        ("Halo", "confirmed")
    ]


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
