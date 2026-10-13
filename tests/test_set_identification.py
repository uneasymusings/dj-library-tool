"""Grouping audio recognition windows into the songs a set played.

The regression data is a real scan of a 62-minute Keinemusik set: Shazam on a 12 s window
every 30 s (tests/data/keinemusik-scan.jsonl).
"""

import json
from pathlib import Path

from djlib.application.set_identification import clock, segments

DATA = Path(__file__).parent / "data"


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
