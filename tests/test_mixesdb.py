"""MixesDB lookups against replies recorded from www.mixesdb.com (tests/data/mixesdb)."""

import asyncio
import json
import logging
import time
from datetime import date
from pathlib import Path

import httpx
import pytest

from djlib.application.set_identification import build_tracklist, mixesdb_listing
from djlib.application.tracklists import parse_tracklist
from djlib.sources import mixesdb

DATA = Path(__file__).parent / "data" / "mixesdb"
EMPTY = {"batchcomplete": True}
MONTREAL = "https://www.mixesdb.com/w/2013-09-27_-_Kaytranada_@_Boiler_Room_Montreal_002"


def reply(*names: str) -> dict:
    """One API reply holding the pages of the recorded replies named."""
    pages = []
    for name in names:
        pages += json.loads((DATA / name).read_text(encoding="utf-8"))["query"]["pages"]
    return {"batchcomplete": True, "query": {"pages": pages}}


def pages(*names: str) -> list[mixesdb.Page]:
    return [
        mixesdb.Page(p["pageid"], p["title"], p["revisions"][0]["slots"]["main"]["content"])
        for p in reply(*names)["query"]["pages"]
    ]


def page(name: str, title: str) -> mixesdb.Page:
    return next(p for p in pages(name) if p.title.startswith(title))


def wikitext_reply(title: str, wikitext: str, pageid: int = 1) -> dict:
    content = {"slots": {"main": {"content": wikitext}}}
    entry = {"pageid": pageid, "ns": 0, "title": title, "revisions": [content]}
    return {"batchcomplete": True, "query": {"pages": [entry]}}


class Wiki:
    """MixesDB's API answering each request with ``answer(query)``; records what was asked."""

    def __init__(self, answer=lambda query: EMPTY):
        self.answer = answer
        self.asked: list[str] = []
        self.params: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "www.mixesdb.com"
        assert request.headers["user-agent"].startswith("djlib/")
        params = dict(request.url.params)
        self.params.append(params)
        query = params.get("gsrsearch") or params.get("geuquery")
        self.asked.append(query)
        answer = self.answer(query)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))


async def lookup(wiki: Wiki, **wanted):
    async with wiki.client() as client:
        return await mixesdb.lookup(client=client, **wanted)


class Clock:
    """A monotonic clock that sleeping moves forward at once."""

    def __init__(self):
        self.at = 1000.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self.at

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.at += seconds


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Requests keep MixesDB's Crawl-delay on a clock that skips the wait."""
    found = Clock()
    monkeypatch.setattr(mixesdb, "SPACING", mixesdb.Spacing(clock=found.now, sleep=found.sleep))
    return found


# Pages


def test_a_page_gives_its_tracks_length_players_and_completeness():
    montreal = page("kaytranada-montreal.json", "2013-09-27").parsed
    assert montreal["duration_seconds"] == 2525.0
    assert montreal["players"] == ["https://youtu.be/-5EQIiabJvk"]
    assert montreal["tracklist"] == "complete"
    assert len(montreal["tracks"]) == 22
    assert montreal["tracks"][1] == ("00:45", "Robert Glasper - Move Love (Kaytranada Edition)")
    assert montreal["tracks"][-1] == ("39:17", "Modjo - Lady (Kaytranada Remix)")


def test_minute_cues_standard_shows_and_chapter_headings():
    old, new, selection = (
        page("ben-ufo-essential-mix.json", title).parsed for title in ("2013", "2021", "2012")
    )
    assert old["duration_seconds"] == 7200.0  # {{StandardShow2h}}, the show's slot
    assert old["radio_show"] and old["lengths"] == []
    assert (old["tracklist"], new["tracklist"], selection["tracklist"]) == (
        "complete",
        "incomplete",
        "incomplete",
    )
    assert old["tracks"][0] == ("00:00", "Guy Evans - AC004 B2 [All Caps]")
    assert old["tracks"][3] == (None, "Drexciya - Danger Bay [Underground Resistance]")  # [00?]
    assert ("1:50:00", "Untold - Ion [Unreleased]") in old["tracks"]  # [110]
    # A comment on the line is not part of the label.
    assert ("1:01:00", "Kassem Mosse - Untitled [Laid - Rec 007]") in old["tracks"]
    assert old["players"][0] == "https://soundcloud.com/benufo/ben-ufo-essential-mix-051013"
    # ";Chapter 1" and the other show sections are headings, not tracks.
    assert selection["tracks"][:5] == [
        (None, "Quantum Jump - Neighbours"),
        (None, "Fudge Finger - Kinski"),
        (None, "Omar S - Sex"),
        (None, "Omar S - Wayne Country"),
        (None, "? - Maceo Plex vs INXS"),
    ]
    assert len(selection["tracks"]) == 32


def test_list_blocks_gaps_and_tracks_played_over_another():
    guy_j = page("cappadocia.json", "2023-07-27 - Guy J").parsed
    assert guy_j["tracks"] == [
        ("00:00", "?"),
        ("11:00", "Guy Gerber - Rainchecks In Montreal (Roy RosenfelD Remix) [Rumors]"),
        ("17:00", "?"),
        ("37:00", "Kamilo Sanclemente Feat. DaBeat - Seriously"),
        ("41:00", "Serge Devant - White Groove [Crosstown Rebels]"),
        ("47:00", "Claudya & Ripperton - Hey Kid (Claudya Mix) [Bedrock]"),
        ("52:00", "Tantum - Stomper [Radikon]"),
        ("57:00", "?"),
    ]
    black_coffee = page("mayan-warrior.json", "2025-08-26").parsed
    assert (None, "Roland Clark - I Get Deep (Acapella) [S&S]") in black_coffee["tracks"]
    assert black_coffee["duration_seconds"] == 12760.0 and black_coffee["lengths"] == [12760.0]
    assert not black_coffee["radio_show"]


def test_wiki_markup_headings_and_other_sections_are_left_out():
    parsed = mixesdb.parse_page((DATA / "markup.wikitext").read_text(encoding="utf-8"))
    assert parsed["tracks"] == [
        ("00:00", "Moodymann - Shades Of Jae [KDJ]"),
        ("04:30", "T. Parrish - Falling Up (Carl Craig Remix) [Third Ear]"),
        ("08:15", "Floorplan - Never Grow Old [M-Plant]"),
        ("12:00", "?"),
        ("15:45", "ID - ID"),
        (None, "Mr. Fingers - Can You Feel It (Spoken Word Mix)"),
        ("1:02:03", "Kerri Chandler - Rain & Shine (Bar A Thym)"),
        ("1:10:00", "Unknown - Untitled"),
        ("1:12:30", "Robert Hood - Minus (M-Plant)"),
        # CD2 starts again at 0, so its times would put its tracks among CD1's.
        (None, "DJ Sprinkles - Grand Central Pt. 1 [Mule Musiq]"),
        (None, "Jeff Mills - The Bells"),
        (None, "Artist - ?"),
        (None, "Lil Louis - French Kiss"),
        (None, "Kevin Saunderson - Good Life [KMS]"),
    ]
    assert parsed["duration_seconds"] == 7090.0
    # The commented-out player is not one of the page's links.
    assert parsed["players"] == [
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://soundcloud.com/someone/some-set/",
    ]
    assert "https://youtu.be/AAAAAAAAAAA" not in parsed["links"]
    assert parsed["tracklist"] == "complete"


def test_a_page_without_a_tracklist_section_or_category():
    parsed = mixesdb.parse_page("== File details ==\n\n{{Player|https://youtu.be/x}}\n")
    assert parsed["tracks"] == [] and parsed["tracklist"] == "unknown"
    assert parsed["duration_seconds"] is None


def test_a_table_of_parts_gives_their_total_and_every_recording_counts():
    daft_punk = page("coachella.json", "2006").parsed  # Pt. 1 0:44:29, Pt. 2 0:33:43, 1:18:12
    assert daft_punk["lengths"] == [4692.0] and daft_punk["duration_seconds"] == 4692.0
    bjork = page("coachella.json", "2007").parsed  # Version 1 and Version 2
    assert bjork["lengths"] == [4228.0, 4542.0] and bjork["duration_seconds"] == 4228.0
    parts_only = mixesdb.parse_page(
        "== File details ==\n{|\n! Pt. !! dur\n|-\n| 1 || 0:44:29\n|-\n| 2 || 0:33:43\n|}"
    )
    assert parts_only["lengths"] == [4692.0]


def test_times_that_start_again_on_the_next_cd_are_left_out():
    parsed = mixesdb.parse_page(
        "== Tracklist ==\n=== CD1 ===\n# [00:00] A1 - One\n# [30:00] A2 - Two\n"
        "# [60:00] A3 - Three\n=== CD2 ===\n# [00:00] B1 - Four\n# [30:00] B2 - Five\n"
        "# [60:00] B3 - Six\n"
    )
    assert [time for time, _ in parsed["tracks"]] == ["00:00", "30:00", "1:00:00", None, None, None]
    found = {"text": mixesdb.tracklist_text(parsed["tracks"]), "page_url": MONTREAL}
    built = build_tracklist(duration=9000, listed=[mixesdb_listing(found)])
    assert [track["artist"] for track in built["tracks"]] == ["A1", "A2", "A3", "B1", "B2", "B3"]


def test_format_characters_are_left_out_of_labels():
    parsed = mixesdb.parse_page(
        "== Tracklist ==\n# [00:00] Evil\u202e Artist - Ti\u200btle\u00ad\n"
    )
    assert parsed["tracks"] == [("00:00", "Evil Artist - Title")]


@pytest.mark.parametrize(
    "wikitext",
    [
        "=" * 8000 + "x",
        "== Tracklist ==\n# " + "[" * 20000,
        "{{Player|" * 8000,
        "[[Category:Tracklist: none|" * 10000,
        "== Tracklist ==\n" + ("# " + "<ref>" * 400 + "\n") * 150,
        "== Tracklist ==\n" + ("# " + "{{" * 500 + "}}" * 500 + "\n") * 150,
        "== Tracklist ==\n" + ("# " + "[//a " * 400 + "\n") * 150,
        "== Tracklist ==\n" + ("# " + "[[a|" * 500 + "\n") * 150,
    ],
    ids=["heading", "brackets", "player", "category", "refs", "templates", "web", "wiki links"],
)
def test_hostile_wikitext_parses_at_once(wikitext):
    # MixesDB is an open wiki, and parsing runs on the event loop, which no timeout can stop.
    started = time.perf_counter()
    mixesdb.Page(1, "2013 - A @ B", wikitext)
    assert time.perf_counter() - started < 1.0


@pytest.mark.parametrize(
    "name,title",
    [
        ("kaytranada-montreal.json", "2013"),
        ("kaytranada-los-angeles.json", "2013"),
        ("ben-ufo-essential-mix.json", "2013"),
        ("ben-ufo-essential-mix.json", "2012"),
        ("cappadocia.json", "2023"),
        ("mayan-warrior.json", "2025"),
        ("fred-again-boiler-room.json", "2022"),
        ("coachella.json", "2007"),
        ("diynamic.json", "2009-09-18"),
        ("rinse-fm.json", "2017"),  # its last times go back, so they are left out
        ("nts-radio.json", "2016"),
    ],
)
def test_the_text_reads_back_as_a_listing_with_every_track(name, title):
    tracks = page(name, title).parsed["tracks"]
    text = mixesdb.tracklist_text(tracks)
    listed = mixesdb_listing({"text": text, "page_url": MONTREAL, "complete": True})
    items, skipped = parse_tracklist(text, MONTREAL)
    # Only lines without an "Artist - Title" (such as "Kaytranada X Shash'U Collabo") are
    # reported back instead of read.
    assert {entry["reason"] for entry in listed["skipped"]} <= {"no “Artist - Title” separator"}
    assert (
        len(listed["entries"]) + len(listed["skipped"]) == len(tracks) == len(items) + len(skipped)
    )
    left_out = {entry["line"] for entry in listed["skipped"]}
    kept = [time for number, (time, _) in enumerate(tracks, 1) if number not in left_out]
    assert [entry["timestamp"] for entry in listed["entries"]] == kept
    assert listed["source"] == "mixesdb" and listed["url"] == MONTREAL and listed["complete"]


def test_markup_text_reads_back_whole_with_ids_kept():
    parsed = mixesdb.parse_page((DATA / "markup.wikitext").read_text(encoding="utf-8"))
    text = mixesdb.tracklist_text(parsed["tracks"])
    assert text.splitlines()[:2] == [
        "1. [00:00] Moodymann - Shades Of Jae [KDJ]",
        "2. [04:30] T. Parrish - Falling Up (Carl Craig Remix) [Third Ear]",
    ]
    listed = mixesdb_listing({"text": text, "page_url": MONTREAL})
    assert listed["skipped"] == [] and len(listed["entries"]) == 14
    unknown = [e["timestamp"] for e in listed["entries"] if e["kind"] == "unknown"]
    assert unknown == ["12:00", "15:45", "1:10:00", None]
    assert listed["entries"][0]["artist"] == "Moodymann"
    assert listed["entries"][0]["title"] == "Shades Of Jae"
    assert listed["complete"] is False


def test_an_untimed_first_track_is_not_read_as_a_heading():
    text = mixesdb.tracklist_text([(None, "Intro Artist - Intro"), ("02:00", "A - B")])
    items, skipped = parse_tracklist(text)
    assert [item.artist for item in items] == ["Intro Artist", "A"] and skipped == []


def test_the_listing_merges_into_a_tracklist():
    found = {
        "text": mixesdb.tracklist_text(page("kaytranada-montreal.json", "2013").parsed["tracks"]),
        "page_url": MONTREAL,
        "complete": True,
    }
    built = build_tracklist(duration=2525, listed=[mixesdb_listing(found)])
    assert len(built["tracks"]) >= 22
    assert built["tracks"][1]["artist"] == "Robert Glasper"


# Links


@pytest.mark.parametrize(
    "url,key",
    [
        ("https://youtu.be/-5EQIiabJvk", "youtube:-5EQIiabJvk"),
        ("https://youtu.be/-5EQIiabJvk?t=60", "youtube:-5EQIiabJvk"),
        ("https://www.youtube.com/watch?v=-5EQIiabJvk&t=1s", "youtube:-5EQIiabJvk"),
        ("http://m.youtube.com/watch?feature=share&v=-5EQIiabJvk", "youtube:-5EQIiabJvk"),
        ("https://music.youtube.com/watch?v=-5EQIiabJvk&list=x", "youtube:-5EQIiabJvk"),
        ("https://www.youtube.com/embed/-5EQIiabJvk", "youtube:-5EQIiabJvk"),
        ("https://youtube.com/live/-5EQIiabJvk?si=abc", "youtube:-5EQIiabJvk"),
        (
            "https://soundcloud.com/platform/kaytranada-boiler-room",
            "soundcloud.com/platform/kaytranada-boiler-room",
        ),
        (
            "http://m.soundcloud.com/Platform/Kaytranada-Boiler-Room/?in=platform/sets/x#t=1",
            "soundcloud.com/platform/kaytranada-boiler-room",
        ),
        ("https://www.mixcloud.com/dj/set-name/", "mixcloud.com/dj/set-name"),
        # A parameter that names the video stays.
        ("https://www.facebook.com/watch/?v=111&ref=share", "facebook.com/watch?v=111"),
        ("https://drive.google.com/open?id=1AbC", "drive.google.com/open?id=1AbC"),
        ("https://www.youtube.com/watch?v=short", None),
        ("https://www.youtube.com/@BoilerRoom", None),
        ("https://soundcloud.com/", None),
        ("ftp://soundcloud.com/a/b", None),
        ("not a link", None),
        (None, None),
    ],
)
def test_upload_links_share_one_spelling(url, key):
    assert mixesdb.url_key(url) == key


def test_links_are_asked_for_as_written_then_with_or_without_www():
    assert mixesdb.link_spellings(
        "https://www.mixcloud.com/NTSRadio/mr-beatnick-24th-may-2016/?utm_source=x"
    ) == [
        "www.mixcloud.com/NTSRadio/mr-beatnick-24th-may-2016",
        "mixcloud.com/NTSRadio/mr-beatnick-24th-may-2016",
    ]
    assert mixesdb.link_spellings("https://m.soundcloud.com/Platform/Set") == [
        "soundcloud.com/Platform/Set",
        "www.soundcloud.com/Platform/Set",
    ]
    assert mixesdb.link_spellings("https://youtu.be/-5EQIiabJvk") == ["youtube:-5EQIiabJvk"]
    assert mixesdb.link_spellings("not a link") == []


def test_page_addresses():
    assert mixesdb.page_url("2013-09-27 - Kaytranada @ Boiler Room Montreal 002") == MONTREAL
    assert mixesdb.page_url("2022-08-31 - Keinemusik (&ME, Rampa) @ Mayan Warrior") == (
        "https://www.mixesdb.com/w/2022-08-31_-_Keinemusik_(%26ME,_Rampa)_@_Mayan_Warrior"
    )
    assert mixesdb.page_url("2020 - Ben Böhmer @ Cercle?").endswith("Ben_B%C3%B6hmer_@_Cercle%3F")


# Searches


def test_searches_go_from_every_key_word_to_any_to_the_uploader_with_the_dj():
    wanted = mixesdb.Wanted(
        "Ben Böhmer live above Cappadocia in Turkey for Cercle", "Deep House Music"
    )
    assert wanted.queries() == [
        "ben böhmer above cappadocia turkey cercle",
        "ben OR böhmer OR above OR cappadocia OR turkey OR cercle OR deep OR house",
        "deep house ben böhmer",
    ]
    boiler_room = mixesdb.Wanted("Kaytranada | Boiler Room: Montreal 🔥 (DJ Set) 2013")
    assert boiler_room.queries() == [
        "kaytranada boiler room montreal 2013",  # the year finds that year's pages
        "kaytranada OR boiler OR room OR montreal",
    ]
    dated = mixesdb.Wanted("&ME - Keinemusik Radio Show (30 Aug 2019)", "Keinemusik")
    assert dated.queries() == [
        "me keinemusik radio show 2019",
        "me OR keinemusik OR radio OR show",
        "keinemusik me",
    ]


async def test_a_page_linking_the_upload_matches_by_url():
    wiki = Wiki(lambda query: reply("kaytranada-montreal.json"))
    found = await lookup(
        wiki,
        title="Kaytranada | Boiler Room: Montreal",
        uploader="Boiler Room",
        duration=2526,
        url="https://www.youtube.com/watch?v=-5EQIiabJvk&t=10s",
    )
    assert wiki.asked == ['"-5EQIiabJvk"']
    assert found == {
        "source": "mixesdb",
        "page_title": "2013-09-27 - Kaytranada @ Boiler Room Montreal 002",
        "page_url": MONTREAL,
        "text": found["text"],
        "complete": True,
        "tracklist": "complete",
        "tracks": 22,
        "duration_seconds": 2525.0,
        "players": ["https://youtu.be/-5EQIiabJvk"],
        "match": {"by": "url", "score": 1.0},
    }
    assert found["text"].splitlines()[1] == (
        "2. [00:45] Robert Glasper - Move Love (Kaytranada Edition)"
    )


async def test_a_soundcloud_upload_is_found_by_its_address():
    wiki = Wiki(lambda query: reply("fred-again-boiler-room.json"))
    found = await lookup(
        wiki,
        title="Fred again.. Boiler Room London",
        url="https://m.soundcloud.com/platform/fred-again-boiler-room-london/?in=sets/x",
    )
    assert wiki.params[0]["generator"] == "exturlusage"
    assert wiki.params[0]["geuquery"] == "soundcloud.com/platform/fred-again-boiler-room-london"
    assert found["match"] == {"by": "url", "score": 1.0}
    assert found["page_title"] == "2022-06-29 - Fred Again.. @ Boiler Room, London"
    assert found["complete"] is False and found["tracklist"] == "incomplete"


async def test_the_same_set_elsewhere_matches_by_title_and_length():
    wiki = Wiki(lambda query: reply("kaytranada-montreal.json") if "montreal" in query else EMPTY)
    found = await lookup(
        wiki,
        title="Kaytranada Boiler Room Montreal DJ Set",
        uploader="Boiler Room",
        duration=2525.12,
        url="https://soundcloud.com/platform/kaytranada-boiler-room",
    )
    assert wiki.asked == [
        "soundcloud.com/platform/kaytranada-boiler-room",
        "kaytranada boiler room montreal",
    ]
    assert found["match"] == {"by": "title_duration", "score": 1.0}
    assert found["page_url"] == MONTREAL


async def test_a_reupload_with_a_wordy_title_is_found_among_any_word_results():
    def answer(query):
        return reply("cappadocia.json") if " OR " in query else EMPTY

    wiki = Wiki(answer)
    found = await lookup(
        wiki,
        title="Ben Böhmer live above Cappadocia in Turkey for Cercle",
        uploader="Deep House Music",
        duration=5069,
        url="https://www.youtube.com/watch?v=uBkWl7NzywE",
    )
    assert len(wiki.asked) == 3
    assert found["page_title"] == "2020-08-31 - Ben Böhmer @ Cercle, Cappadocia, Turkey"
    assert found["match"]["by"] == "title_duration" and found["match"]["score"] >= 0.9
    assert found["text"].splitlines()[0] == "1. [00:00] ?"


async def test_names_in_brackets_and_more_words_still_match():
    wiki = Wiki(lambda query: reply("mayan-warrior.json"))
    found = await lookup(
        wiki,
        title="Keinemusik (&ME, Rampa, Adam Port) - Mayan Warrior - Burning Man 2022",
        uploader="MayanWarrior",
        duration=8987,
    )
    assert found["page_title"].startswith("2022-08-31 - Keinemusik")
    assert found["match"] == {"by": "title_duration", "score": 1.0}


@pytest.mark.parametrize(
    "title,duration",
    [
        # Same DJ and series, another city, with this set's length.
        ("Kaytranada | Boiler Room: Los Angeles", 2526),
        # Same DJ, same city, another length (and so another set).
        ("Kaytranada | Boiler Room: Montreal", 2884),
        # Only part of the set.
        ("Best of Kaytranada Boiler Room Montreal, House", 528),
        # Same DJ and series without the city: the page names a place the upload does not.
        ("Kaytranada | Boiler Room", 2884),
    ],
)
async def test_the_same_dj_elsewhere_or_another_length_is_no_match(title, duration):
    wiki = Wiki(lambda query: reply("kaytranada-montreal.json", "kaytranada-los-angeles.json"))
    assert await lookup(wiki, title=title, uploader="Boiler Room", duration=duration) is None


@pytest.mark.parametrize(
    "fixture,title,duration",
    [
        ("cappadocia.json", "Guy J live at Cercle in Cappadocia, Turkey", 5068),
        ("cappadocia.json", "WhoMadeWho | Cercle, Cappadocia", 5068),
        ("mayan-warrior.json", "Black Coffee @ Mayan Warrior, Burning Man 2022", 8986),
        ("mayan-warrior.json", "Black Coffee @ Mayan Warrior, Burning Man", 8986),
    ],
)
async def test_another_dj_at_the_same_place_is_no_match(fixture, title, duration):
    wiki = Wiki(lambda query: reply(fixture))
    assert await lookup(wiki, title=title, duration=duration) is None


async def test_every_dj_the_upload_names_must_be_on_the_page():
    wiki = Wiki(lambda query: reply("rinse-fm.json"))
    found = await lookup(wiki, title="Mumdance b2b Ben UFO - Rinse FM 2017-12-12", duration=7190)
    assert found["page_title"] == "2017-12-12 - Mumdance b2b Ben UFO - Rinse FM"
    for title in (
        "Mumdance b2b Ben UFO b2b Pearson Sound - Rinse FM 2017-12-12",
        "Ben UFO b2b Pearson Sound - SaveFabric, Rinse FM 2016-09-03",
    ):
        assert await lookup(wiki, title=title, duration=7190) is None


async def test_the_uploader_stands_in_for_the_dj_only_when_the_title_names_none():
    wiki = Wiki(lambda query: reply("mayan-warrior.json"))
    channel = {"uploader": "Keinemusik", "duration": 8987}
    found = await lookup(wiki, title="Mayan Warrior - Burning Man 2022", **channel)
    assert found["page_title"].startswith("2022-08-31 - Keinemusik")
    # One member's set on the collective's channel is not the collective's set.
    assert await lookup(wiki, title="Rampa | Mayan Warrior - Burning Man 2022", **channel) is None


PART_TWO = "https://soundcloud.com/blogos4/daft-punk-live-coachella-festival-2006-04-29-pt-2"


async def test_one_part_of_a_set_is_no_match():
    wiki = Wiki(lambda query: reply("coachella.json"))
    # The page links part 2, but lists the tracks of both parts.
    part_two = await lookup(
        wiki, title="Daft Punk Coachella 2006 pt 2", duration=2023, url=PART_TWO
    )
    assert part_two is None and len(wiki.asked) == 1
    assert await lookup(wiki, title="Daft Punk - Live at Coachella 2006", duration=2669) is None
    whole = await lookup(wiki, title="Daft Punk - Live at Coachella 2006 (Full Set)", duration=4692)
    assert whole["match"] == {"by": "title_duration", "score": 1.0}
    assert whole["duration_seconds"] == 4692.0 and whole["tracks"] == 12
    later_version = await lookup(wiki, title="Björk @ Coachella 2007", duration=4540)
    assert later_version["page_title"].startswith("2007-04-27 - Björk")


async def test_without_a_length_only_a_near_exact_title_matches():
    wiki = Wiki(lambda query: reply("kaytranada-montreal.json", "kaytranada-los-angeles.json"))
    found = await lookup(wiki, title="Kaytranada | Boiler Room: Montreal")
    assert found["match"] == {"by": "title", "score": 1.0}
    assert await lookup(wiki, title="Kaytranada Boiler Room") is None
    assert await lookup(wiki, title="Kaytranada Boiler Room Montreal live with friends") is None


@pytest.mark.parametrize(
    "title,days",
    [
        ("&ME - Keinemusik Radio Show (2019-08-30)", {date(2019, 8, 30)}),
        ("Solomun - Diynamic (Proton Radio) 16.12.2011", {date(2011, 12, 16)}),
        ("Solomun | 16 Dec 2011", {date(2011, 12, 16)}),
        ("Solomun | December 16th, 2011", {date(2011, 12, 16)}),
        ("Ben UFO 05/10/2013", {date(2013, 10, 5), date(2013, 5, 10)}),  # either way round
        ("Ben UFO 31.02.2013", set()),
        ("Ben UFO @ Fabric 25 (2011)", set()),
    ],
)
def test_days_in_upload_titles(title, days):
    assert mixesdb.Wanted(title).days == days


@pytest.mark.parametrize(
    "fixture,title,duration",
    [
        # Another day's episode of the show, at the show's length.
        ("diynamic.json", "Solomun - Diynamic Radio Show (Proton Radio) 16.12.2011", 3598),
        ("keinemusik-radio-show.json", "&ME - Keinemusik Radio Show (2019-08-30)", 3598),
        # No day: every episode has the show's slot ({{StandardShow1h}}) as its length.
        ("diynamic.json", "Solomun - Diynamic, Proton Radio", 3601),
        ("diynamic.json", "Solomun - Diynamic, Proton Radio", None),
        ("keinemusik-radio-show.json", "&ME | Keinemusik Radio Show", 3600),
        ("ben-ufo-essential-mix.json", "Ben UFO – Essential Mix (2013)", 7190),
        ("ben-ufo-essential-mix.json", "Ben UFO - Essential Mix", 7190),
        ("ben-ufo-essential-mix.json", "Ben UFO - Essential Mix", None),
    ],
)
async def test_a_radio_show_episode_needs_its_day(fixture, title, duration):
    wiki = Wiki(lambda query: reply(fixture))
    assert await lookup(wiki, title=title, duration=duration) is None


@pytest.mark.parametrize(
    "fixture,title,page_title",
    [
        (
            "diynamic.json",
            "Solomun - Diynamic Radio Show (Proton Radio) 18.11.2011",
            "2011-11-18 - Solomun - Diynamic, Proton Radio",
        ),
        (
            "diynamic.json",
            "Solomun - Hello Weekend (Diynamic, Proton Radio) 18th September 2009",
            "2009-09-18 - Solomun - Hello Weekend! (Diynamic, Proton Radio)",
        ),
        (
            "keinemusik-radio-show.json",
            "&ME - Keinemusik Radio Show (26/04/2019)",
            "2019-04-26 - &ME - Keinemusik Radio Show",
        ),
        (
            "ben-ufo-essential-mix.json",
            "Ben UFO – Essential Mix 05.10.2013",
            "2013-10-05 - Ben UFO - Essential Mix",
        ),
    ],
)
async def test_a_radio_show_episode_with_its_day_matches(fixture, title, page_title):
    found = await lookup(Wiki(lambda query: reply(fixture)), title=title)
    assert found["page_title"] == page_title
    assert found["match"]["by"] == "title_date" and found["match"]["score"] >= 0.9


async def test_a_day_that_disagrees_rules_a_page_out_and_is_no_number():
    wiki = Wiki(lambda query: reply("kaytranada-montreal.json"))
    title = "Kaytranada Boiler Room Montreal"
    found = await lookup(wiki, title=f"{title} 2013-09-27", duration=2526)
    assert found["match"] == {"by": "title_date", "score": 1.0}  # "09" and "27" are no numbers
    assert (await lookup(wiki, title=f"{title} 28.09.2013"))["match"]["by"] == "title_date"
    assert await lookup(wiki, title=f"{title} 2013-09-29", duration=2526) is None


def episode(title: str, length: str, pageid: int) -> dict:
    """A reply page for one episode of a show, with its file's length and two tracks."""
    details = "== File details ==\n{|{{NormalTableFormat}}\n! dur\n|-\n| " + length + "\n|}\n"
    wikitext = details + "== Tracklist ==\n# [00:00] A - One\n# [30:00] B - Two\n"
    return wikitext_reply(title, wikitext, pageid)["query"]["pages"][0]


async def test_another_episode_of_the_same_show_is_a_rival_whatever_its_score():
    episodes = [
        episode("2019-04-26 - &ME - Keinemusik Radio Show", "1:00:00", 1),
        episode("2019-05-24 - &ME - Hello Weekend (Keinemusik Radio Show)", "1:00:20", 2),
    ]
    wiki = Wiki(lambda query: {"batchcomplete": True, "query": {"pages": episodes}})
    # The second page scores too low to count as a match, but it may be the upload.
    assert await lookup(wiki, title="&ME | Keinemusik Radio Show", duration=3600) is None
    found = await lookup(wiki, title="&ME | Keinemusik Radio Show 26.04.2019", duration=3600)
    assert found["page_title"] == "2019-04-26 - &ME - Keinemusik Radio Show"


async def test_a_linked_page_without_tracks_ends_the_lookup():
    empty_page = wikitext_reply(
        "2013-09-27 - Kaytranada @ Boiler Room Montreal 002",
        "{{Player|https://youtu.be/-5EQIiabJvk}}\n\n[[Category:Tracklist: none]]",
    )
    wiki = Wiki(lambda query: empty_page)
    found = await lookup(
        wiki, title="Kaytranada | Boiler Room: Montreal", url="https://youtu.be/-5EQIiabJvk"
    )
    assert found is None and len(wiki.asked) == 1


async def test_two_pages_linking_the_upload_are_no_answer():
    def answer(query):
        one = wikitext_reply("2013 - A @ B", "{{Player|https://youtu.be/-5EQIiabJvk}}", 1)
        two = wikitext_reply("2013 - A @ C", "{{Player|https://youtu.be/-5EQIiabJvk}}", 2)
        one["query"]["pages"] += two["query"]["pages"]
        return one

    wiki = Wiki(answer)
    assert await lookup(wiki, title="A @ B", url="https://youtu.be/-5EQIiabJvk") is None
    assert len(wiki.asked) == 1


async def test_a_link_stored_with_www_is_asked_for_with_a_spare_request():
    stored = "www.mixcloud.com/NTSRadio/mr-beatnick-24th-may-2016"
    wiki = Wiki(lambda query: reply("nts-radio.json") if query == stored else EMPTY)
    url = "https://mixcloud.com/NTSRadio/mr-beatnick-24th-may-2016/"
    found = await lookup(wiki, title="Mr. Beatnick NTS", url=url)
    assert wiki.asked == [
        "mixcloud.com/NTSRadio/mr-beatnick-24th-may-2016",
        "mr beatnick nts",
        "mr OR beatnick OR nts",
        stored,
    ]
    assert found["page_title"] == "2016-05-24 - Mr. Beatnick - NTS Radio"
    assert found["match"] == {"by": "url", "score": 1.0}


async def test_requests_are_capped():
    wiki = Wiki()
    found = await lookup(
        wiki,
        title="Some DJ | Some Room: Some City",
        uploader="Some Channel",
        duration=3600,
        url="https://www.youtube.com/watch?v=-5EQIiabJvk",
    )
    assert found is None and len(wiki.asked) == mixesdb.MAX_REQUESTS == 4
    without_link = Wiki()
    await lookup(without_link, title="Some DJ | Some Room: Some City", uploader="Some Channel")
    assert len(without_link.asked) == 3
    assert await lookup(Wiki(), title="") is None


def failing(kind):
    def answer(query):
        if kind == "unreachable":
            raise httpx.ConnectError("no route")
        if kind == "timeout":
            raise httpx.ReadTimeout("slow")
        if kind == "invalid":
            return httpx.Response(200, text="<html>maintenance</html>")
        if kind == "api error":
            return {"error": {"code": "maxlag", "info": "Waiting for a database server"}}
        return httpx.Response(int(kind), text="busy")

    return answer


@pytest.mark.parametrize("kind", ["unreachable", "timeout", "invalid", "api error", "429", "503"])
async def test_network_failures_are_no_tracklist(kind, caplog):
    caplog.set_level(logging.DEBUG, logger="djlib.sources.mixesdb")
    wiki = Wiki(failing(kind))
    found = await lookup(
        wiki, title="Kaytranada | Boiler Room: Montreal", url="https://youtu.be/-5EQIiabJvk"
    )
    assert found is None and len(wiki.asked) == 1  # the first failure ends the lookup
    assert "MixesDB lookup stopped" in caplog.text


@pytest.mark.parametrize("digit", ["\u1369", "\U00010a40", "\u19da"])
async def test_digits_that_are_no_numbers_do_not_stop_a_lookup(digit):
    wiki = Wiki(lambda query: reply("kaytranada-montreal.json"))
    found = await lookup(wiki, title=f"Kaytranada | Boiler Room: Montreal {digit}", duration=2526)
    assert found["page_url"] == MONTREAL


def entry(title: str, index, pageid: int = 1) -> dict:
    content = {"slots": {"main": {"content": "== Tracklist ==\n# A - B"}}}
    return {"pageid": pageid, "title": title, "index": index, "revisions": [content]}


@pytest.mark.parametrize(
    "body",
    [
        "[" * 100_000 + "]" * 100_000,
        json.dumps({"query": {"pages": [entry("2013 - C @ D", "1"), entry("2013 - C @ E", 2, 2)]}}),
        json.dumps({"query": {"pages": 5}}),
        json.dumps({"query": {"pages": [entry("2013 - A @ B \ud800", 1)]}}),
    ],
    ids=["deeply nested", "an index that is text", "pages that are a number", "lone surrogate"],
)
async def test_broken_replies_are_no_tracklist(body):
    wiki = Wiki(lambda query: httpx.Response(200, text=body))
    assert await lookup(wiki, title="A @ B", url="https://youtu.be/-5EQIiabJvk") is None


async def test_a_slow_lookup_gives_up(monkeypatch):
    monkeypatch.setattr(mixesdb, "DEADLINE", 0.05)

    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json=EMPTY)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as client:
        assert await mixesdb.lookup(title="Kaytranada", client=client) is None


async def test_the_callers_client_stays_open():
    wiki = Wiki(lambda query: reply("kaytranada-montreal.json"))
    async with wiki.client() as client:
        await mixesdb.lookup(title="x", url="https://youtu.be/-5EQIiabJvk", client=client)
        assert not client.is_closed


async def test_requests_keep_mixesdb_s_crawl_delay(clock):
    """robots.txt on mixesdb.com asks for 4 seconds between requests, across sets too."""
    asked_at = []

    def answer(query):
        asked_at.append(clock.now())
        return EMPTY

    wiki = Wiki(answer)
    await lookup(
        wiki,
        title="Some DJ | Some Room: Some City",
        uploader="Some Channel",
        duration=3600,
        url="https://www.youtube.com/watch?v=-5EQIiabJvk",
    )
    first = len(asked_at)
    assert first == mixesdb.MAX_REQUESTS == 4  # spacing keeps every fallback lookup
    await lookup(wiki, title="Kaytranada | Boiler Room: Montreal", duration=2526)
    assert len(asked_at) > first
    gaps = [later - earlier for earlier, later in zip(asked_at, asked_at[1:], strict=False)]
    assert min(gaps) >= mixesdb.CRAWL_DELAY == 4
    # Waits for a turn come on top of a lookup's 20 seconds of asking.
    assert mixesdb.DEADLINE == 20


async def test_sets_looked_up_at_once_each_get_their_answer(monkeypatch):
    """Four `djlib set` runs at once: the last in line still gets MixesDB's page, because
    waiting for a turn doesn't count against its deadline (time scaled down 1:40)."""
    spacing = mixesdb.Spacing(delay=0.1)
    monkeypatch.setattr(mixesdb, "SPACING", spacing)
    monkeypatch.setattr(mixesdb, "DEADLINE", 0.5)  # shorter than the queue of turns
    turns = []

    def reserve():
        wait = mixesdb.Spacing.reserve(spacing)
        turns.append(spacing.next_at - spacing.delay)
        return wait

    monkeypatch.setattr(spacing, "reserve", reserve)

    def answer(query):
        return reply("kaytranada-montreal.json") if "kaytranada-boiler-room" in query else EMPTY

    async def one(**wanted):
        return await lookup(Wiki(answer), strict=True, **wanted)  # strict: a timeout raises

    elsewhere = [
        {
            "title": f"DJ {n} | Room {n}",
            "uploader": "x",
            "duration": 3600,
            "url": f"https://youtu.be/{n * 11}",
        }
        for n in ("a", "b", "c")
    ]
    *others, kaytranada = await asyncio.gather(
        *(one(**wanted) for wanted in elsewhere),
        one(
            title="Kaytranada | Boiler Room: Montreal",
            duration=2526,
            url="https://soundcloud.com/platform/kaytranada-boiler-room",
        ),
    )
    assert others == [None, None, None] and kaytranada["page_url"] == MONTREAL
    # One request per turn, each turn a delay after the last; the queue outlasted a deadline.
    gaps = [later - earlier for earlier, later in zip(turns, turns[1:], strict=False)]
    assert min(gaps) == pytest.approx(0.1) and turns[-1] - turns[0] > mixesdb.DEADLINE


def test_spacing_waits_only_what_is_left_of_the_delay():
    clock = Clock()
    spacing = mixesdb.Spacing(clock=clock.now)
    assert spacing.reserve() == 0
    clock.at += 1.5  # the answer took a second and a half
    assert spacing.reserve() == 2.5
    assert spacing.reserve() == 6.5  # another set's request, right after: the turn after that
    clock.at += 20.0  # nothing asked for a while
    assert spacing.reserve() == 0


async def test_a_set_looked_up_moments_ago_asks_mixesdb_nothing(monkeypatch):
    from djlib.application import identification

    wiki = Wiki(lambda query: reply("kaytranada-montreal.json"))

    async def looked_up(**wanted):
        async with wiki.client() as client:
            return await mixesdb.lookup(client=client, **wanted)

    monkeypatch.setattr(identification, "mixesdb_lookup", looked_up)
    page = {
        "url": "https://soundcloud.com/platform/kaytranada-boiler-room",
        "title": "Kaytranada | Boiler Room: Montreal",
        "duration_seconds": 2526,
    }
    found, answered = await identification.mixesdb_answer(page)
    assert answered and found["page_url"] == MONTREAL
    asked = len(wiki.asked)
    again, answered = await identification.mixesdb_answer(page)
    assert answered and again == found and len(wiki.asked) == asked
