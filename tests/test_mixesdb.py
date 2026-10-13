"""MixesDB lookups against replies recorded from www.mixesdb.com (tests/data/mixesdb)."""

import asyncio
import json
import logging
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
    assert old["duration_seconds"] == 7200.0  # {{StandardShow2h}}
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
    assert black_coffee["duration_seconds"] == 12760.0


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
        ("00:30", "DJ Sprinkles - Grand Central Pt. 1 [Mule Musiq]"),
        (None, "Jeff Mills - The Bells"),
        ("1:05:20", "Artist - ?"),
        (None, "Lil Louis - French Kiss"),
        ("1:20:00", "Kevin Saunderson - Good Life [KMS]"),
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
    assert unknown == ["12:00", "15:45", "1:10:00", "1:05:20"]
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
        "kaytranada boiler room montreal",
        "kaytranada OR boiler OR room OR montreal",
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


async def test_without_a_length_only_a_near_exact_title_matches():
    wiki = Wiki(lambda query: reply("kaytranada-montreal.json", "kaytranada-los-angeles.json"))
    found = await lookup(wiki, title="Kaytranada | Boiler Room: Montreal")
    assert found["match"] == {"by": "title", "score": 1.0}
    assert await lookup(wiki, title="Kaytranada Boiler Room") is None
    assert await lookup(wiki, title="Kaytranada Boiler Room Montreal live with friends") is None


async def test_the_same_show_in_two_years_needs_the_year():
    wiki = Wiki(lambda query: reply("ben-ufo-essential-mix.json"))
    assert await lookup(wiki, title="Ben UFO - Essential Mix", duration=7190) is None
    assert await lookup(wiki, title="Ben UFO - Essential Mix") is None
    found = await lookup(wiki, title="Ben UFO – Essential Mix (2013)", duration=7190)
    assert found["page_title"] == "2013-10-05 - Ben UFO - Essential Mix"
    assert found["match"]["by"] == "title_duration"


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
