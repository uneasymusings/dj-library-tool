"""`djlib set LINK` listens to a set when its tracklist is missing, has IDs or is too short.

Everything runs through the real coordinator; the page, the download and the recognizer are
the fakes of tests/test_identification.py.
"""

import json

import pytest

from djlib.application import identification
from tests.test_headline_workflow import build
from tests.test_identification import URL, Setup
from tests.test_identify_cli import LISTED, events, fakes, invoke
from tests.test_rekordbox_push import FakeRekordbox


@pytest.fixture
def setup(monkeypatch):
    return fakes(monkeypatch)


def owned(library_http, audio_factory):
    build(library_http, [audio_factory("halo.wav", frequency=330)], [("Lumen", "Halo")])


def test_set_listens_when_the_published_tracklist_has_ids(
    application, library_http, setup, audio_factory, monkeypatch
):
    owned(library_http, audio_factory)
    setup.page["description"] = LISTED
    fake = FakeRekordbox(monkeypatch)
    reply = invoke(application, "set", URL)
    assert reply.exit_code == 0, reply.output
    envelope = json.loads(reply.stdout)
    result = envelope["result"]
    assert (result["name"], result["songs"], result["owned"]) == ("Friday Set", 3, 1)
    identified = result["identified"]
    assert identified["counts"]["likely"] == 1 and identified["reused"] is False
    # An ID is not a named song, even a likely one, though its song was heard.
    assert identified["coverage"]["percent"] == 67
    assert identified["coverage"]["percent_heard"] == 92
    assert identified["tracklist_file"].endswith("tracklist.txt") and identified["job_id"]
    missing = {item["position"]: item for item in result["missing"]}
    assert missing[2]["label"].startswith("ID @ ")
    assert missing[2]["notes"].startswith("ID (likely Bicep - Glue)")
    # How sure djlib is goes with each song, so an assistant can suggest a listen.
    assert missing[2]["confidence"] == "likely" and missing[3]["confidence"] == "confirmed"
    assert result["owned_to_check"] == []  # Halo, the song owned, was heard: confirmed
    assert fake.imported[-1].name == "Friday Set.m3u8"
    assert identification.UNOFFICIAL_SHAZAM in envelope["warnings"]
    assert len(setup.inspected) == 1  # the identification reused the page djlib set read
    # Scripts and assistants learn why djlib chose to listen.
    started = events(reply)[0]
    assert started["event"] == "identify_started" and started["identify_reason"] == "ids"

    # Once identified, the same set reuses what djlib heard.
    again = json.loads(invoke(application, "set", URL).stdout)["result"]
    assert again["identified"]["reused"] is True and len(setup.fetched) == 1


def test_a_set_heard_before_comes_back_while_its_page_does_not_answer(
    application, library_http, setup, audio_factory, monkeypatch
):
    """YouTube or SoundCloud fails for a moment: a set djlib already heard is shown from
    what it read and heard then, saying so, instead of a "safe to retry" error."""
    from djlib.domain.errors import AppError

    owned(library_http, audio_factory)
    setup.page["description"] = LISTED
    FakeRekordbox(monkeypatch)
    first = json.loads(invoke(application, "set", URL).stdout)["result"]

    async def down(url, comments=0):
        raise AppError("SOURCE_FAILED", "The site didn't answer.", 502, True)

    import djlib.interfaces.service as service_module

    monkeypatch.setattr(service_module, "inspect_source", down)
    monkeypatch.setattr(identification.web, "inspect_source", down)
    reply = invoke(application, "set", URL)
    assert reply.exit_code == 0, reply.output
    envelope = json.loads(reply.stdout)
    again = envelope["result"]
    assert again["request_id"] == first["request_id"] and again["identified"]["reused"] is True
    assert any(
        warning.startswith("Couldn't reach YouTube just now; this is what djlib read and heard on ")
        for warning in envelope["warnings"]
    )
    # A set never heard still says the page couldn't be read.
    other = invoke(application, "set", "https://www.youtube.com/watch?v=otherset123")
    assert json.loads(other.stdout)["error"]["code"] == "SOURCE_FAILED"


def test_a_saved_page_answers_like_a_fresh_read_and_is_read_once(
    application, library_http, setup, audio_factory, monkeypatch
):
    """While the site times out, the saved page has the keys and the comment count a fresh
    read would, and the identification `djlib set` starts doesn't read the page again."""
    from djlib.domain.errors import AppError

    owned(library_http, audio_factory)
    setup.page["description"] = LISTED
    setup.page["comments"] = [{"text": f"comment {n}", "author": "x"} for n in range(50)]
    FakeRekordbox(monkeypatch)
    invoke(application, "set", URL)
    fresh = library_http.post("/sources/inspect", json={"url": URL, "comments": 1000}).json()
    tries = []

    async def slow(url, comments=0):
        tries.append(comments)
        raise AppError("SOURCE_TIMEOUT", "The site didn't answer in time.", 504, True)

    import djlib.interfaces.service as service_module

    monkeypatch.setattr(service_module, "inspect_source", slow)
    monkeypatch.setattr(identification.web, "inspect_source", slow)
    for comments in (5, 0):
        saved = library_http.post("/sources/inspect", json={"url": URL, "comments": comments})
        result = saved.json()["result"]
        assert result["from_saved_page"] is True and len(result["comments"]) == comments
        listed = set() if comments else {"named_in_comments"}  # only asked with comments
        assert set(fresh["result"]) - listed <= set(result)
        assert result["identity_evidence"] == identification.web.PAGE_EVIDENCE
        assert result["description_truncated"] is False
        assert result["duration_seconds"] == 300 and type(result["duration_seconds"]) is int
    # Two days on, with nothing read in this process: one try, then what djlib heard.
    page = identification.set_folder(application.workspace, URL) / "page.json"
    kept = json.loads(page.read_text(encoding="utf-8"))
    page.write_text(json.dumps({**kept, "read_at": "2026-01-01T00:00:00+00:00"}), "utf-8")
    monkeypatch.setattr(identification, "PAGES", {})
    monkeypatch.setattr(identification, "PAGE_FAILURES", {})
    tries.clear()
    reply = invoke(application, "set", URL)
    assert reply.exit_code == 0, reply.output
    assert json.loads(reply.stdout)["result"]["identified"]["reused"] is True
    assert tries == [identification.COMMENTS]


def test_a_tracklist_published_the_same_day_is_read(
    application, library_http, setup, audio_factory, monkeypatch
):
    """Hours after the upload, its uploader names the ID and adds a song: checking the set
    again that day reads the page again and takes them."""
    owned(library_http, audio_factory)
    setup.page["description"] = LISTED
    FakeRekordbox(monkeypatch)
    invoke(application, "set", URL)
    setup.page["description"] = (
        "Lumen - Halo\nBicep - Glue\nNia Okoro - Slow Burn\nRival Consoles - Persona\n"
    )
    read = len(setup.inspected)
    again = json.loads(invoke(application, "set", URL).stdout)["result"]
    assert len(setup.inspected) == read + 1 and again["identified"]["reused"] is True
    labels = [item["label"] for item in again["missing"]]
    assert "Rival Consoles - Persona" in labels and "Bicep - Glue" in labels
    assert not any(label.startswith("ID") for label in labels)


def test_a_failed_mixesdb_lookup_is_asked_again_by_the_next_check(
    application, library_http, setup, audio_factory, monkeypatch
):
    """MixesDB doesn't answer the first time; checking the set again the same day without
    listening asks it again instead of saying it has no tracklist."""
    from djlib.domain.errors import AppError

    found = {
        "source": "mixesdb",
        "page_title": "2026-10-01 - Lumen @ Friday",
        "page_url": "https://www.mixesdb.com/w/2026-10-01_-_Lumen_@_Friday",
        "text": "1. Lumen - Halo\n2. Nia Okoro - Slow Burn\n3. Bicep - Glue\n",
        "complete": True,
        "tracklist": "complete",
    }
    down = [True]

    async def lookup(**wanted):
        if down[0]:
            raise AppError("SOURCE_TIMEOUT", "MixesDB did not answer in time.", 504, True)
        return found

    monkeypatch.setattr(identification, "mixesdb_lookup", lookup)
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    first = json.loads(invoke(application, "set", URL).stdout)["result"]
    assert first["identified"] and first["mixesdb"] is None
    down[0] = False
    again = json.loads(invoke(application, "set", URL, "--no-identify").stdout)["result"]
    assert again["mixesdb"]["page_url"] == found["page_url"]
    assert [item["label"] for item in again["missing"]] == [
        "Nia Okoro - Slow Burn",
        "Bicep - Glue",
    ]


def test_a_set_checked_again_with_new_notes_keeps_its_list_crate_and_playlist(
    application, library_http, setup, audio_factory, monkeypatch
):
    """Re-running a set after an upgrade that words its notes differently (or adds another
    spelling) updates the saved list in place: no second crate, no "Friday Set (2)"."""
    from djlib.application import set_identification

    owned(library_http, audio_factory)
    setup.page["description"] = LISTED
    fake = FakeRekordbox(monkeypatch)
    first = json.loads(invoke(application, "set", URL).stdout)["result"]
    worded = set_identification._notes
    monkeypatch.setattr(set_identification, "_notes", lambda track: f"{worded(track)}; reworded")
    again = json.loads(invoke(application, "set", URL).stdout)["result"]
    assert again["request_id"] == first["request_id"] and again["revision"] > first["revision"]
    assert again["missing"][0]["notes"].endswith("; reworded")
    assert again["collection_id"] == first["collection_id"]
    assert again["playlist"] == "Friday Set"
    assert again["rekordbox"]["status"] == "already_in_rekordbox"
    assert [path.name for path in fake.imported] == ["Friday Set.m3u8"]


def test_set_keeps_the_fast_path_when_the_published_tracklist_is_complete(
    application, library_http, setup, audio_factory, monkeypatch
):
    owned(library_http, audio_factory)
    setup.page["description"] = "Lumen - Halo\nBicep - Glue\n"
    FakeRekordbox(monkeypatch)
    result = json.loads(invoke(application, "set", URL).stdout)["result"]
    assert result["identified"] is None and setup.fetched == []
    assert (result["songs"], result["owned"]) == (2, 1)


def test_no_identify_uses_the_published_tracklist_as_it_is(
    application, library_http, setup, audio_factory, monkeypatch
):
    owned(library_http, audio_factory)
    setup.page["description"] = LISTED
    FakeRekordbox(monkeypatch)
    result = json.loads(invoke(application, "set", URL, "--no-identify").stdout)["result"]
    assert result["identified"] is None and setup.fetched == []
    assert [item["label"] for item in result["missing"]] == ["ID - ID", "Nia Okoro - Slow Burn"]
    setup.page["description"] = ""
    error = json.loads(invoke(application, "set", URL, "--no-identify").stdout)["error"]
    assert error["code"] == "TRACKLIST_NOT_FOUND" and "Leave out --no-identify" in error["message"]


def test_set_identify_listens_even_to_a_complete_tracklist(
    application, library_http, setup, audio_factory, monkeypatch
):
    owned(library_http, audio_factory)
    setup.page["description"] = "Lumen - Halo\nNia Okoro - Slow Burn\n"
    FakeRekordbox(monkeypatch)
    result = json.loads(invoke(application, "set", URL, "--identify").stdout)["result"]
    # Shazam heard a song the description left out; it becomes a track of its own.
    titles = [item["label"] for item in result["missing"]]
    assert "Bicep - Glue" in titles and result["identified"]["counts"]["tracks"] == 3


def test_without_song_recognition_set_falls_back_or_says_how_to_install_it(
    application, library_http, audio_factory, monkeypatch
):
    fakes = Setup(monkeypatch)

    def missing(name=None):
        raise identification.AppError("DEPENDENCY_REQUIRED", "Song recognition is not installed.")

    monkeypatch.setattr(identification, "recognizer", missing)
    owned(library_http, audio_factory)
    fakes.page["description"] = LISTED
    FakeRekordbox(monkeypatch)
    envelope = json.loads(invoke(application, "set", URL).stdout)
    assert envelope["result"]["identified"] is None and envelope["result"]["songs"] == 3
    assert envelope["warnings"][0] == (
        "djlib didn't listen to the set (song recognition isn't installed), so it uses the "
        "tracklist in its description."
    )
    # The fix is a next step of its own, in one piece.
    assert envelope["result"]["listen_fix"] == identification.INSTALLER
    pretty = invoke(application, "set", URL, "--no-rekordbox", pretty=True)
    assert identification.INSTALLER in pretty.stdout
    assert "listening to the set" not in pretty.stderr  # it never started
    explicit = json.loads(invoke(application, "set", URL, "--identify").stdout)["error"]
    assert explicit["code"] == "DEPENDENCY_REQUIRED"
    fakes.page["description"] = ""
    missing = json.loads(invoke(application, "set", URL).stdout)["error"]
    assert missing["code"] == "TRACKLIST_NOT_FOUND"
    # Why djlib couldn't listen comes first, and its fix is the next step.
    assert missing["message"].startswith(
        "djlib couldn't listen to this set (song recognition isn't installed), and it has no "
        "tracklist in its description or chapters, and none on MixesDB."
    )
    assert missing["details"]["next"] == identification.INSTALLER
    assert missing["details"]["tracklist_next"] == f"djlib set paste --source '{URL}'"


def test_set_merges_a_1001tracklists_file_with_what_it_hears(
    application, library_http, setup, audio_factory, monkeypatch, tmp_path
):
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    listing = tmp_path / "tracklist.txt"
    listing.write_text("1001Tracklists\n" + LISTED, encoding="utf-8")
    # A link with the file to merge…
    reply = invoke(application, "set", URL, "--tracklist", listing)
    result = json.loads(reply.stdout)["result"]
    assert result["identified"]["counts"]["likely"] == 1 and result["songs"] == 3
    assert events(reply)[0]["identify_reason"] == "asked"
    # …or the file with its link (the 1001Tracklists path); identified before, so free.
    result = json.loads(invoke(application, "set", listing, "--source", URL).stdout)["result"]
    assert result["identified"]["reused"] is True and result["songs"] == 3
    assert result["source"]["url"] == URL and len(setup.fetched) == 1


def test_identification_options_need_a_set_link(application, monkeypatch, tmp_path):
    FakeRekordbox(monkeypatch)
    listing = tmp_path / "set.txt"
    listing.write_text(LISTED, encoding="utf-8")
    for arguments, words in (
        ([listing, "--identify"], "--source LINK"),
        (["shazam", "--identify"], "Shazams"),
        ([URL, "--tracklist", listing, "--no-identify"], "--no-identify"),
    ):
        error = json.loads(invoke(application, "set", *arguments).stdout)["error"]
        assert error["code"] == "INPUT_INVALID" and words in error["message"], arguments


def test_set_says_why_it_listens_before_a_long_listen(
    application, library_http, setup, audio_factory, monkeypatch
):
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    setup.page["description"] = ""
    reply = invoke(application, "set", URL, "--no-rekordbox", pretty=True, columns=100)
    assert reply.exit_code == 0, reply.output
    said = " ".join(reply.stderr.split())
    assert (
        "The page has no tracklist, so djlib is listening to the set to find the songs. An "
        "hour-long set usually takes 5–10 minutes, up to half an hour when Shazam is busy; "
        "Ctrl-C stops it, and the same command carries on." in said
    )
    # Identified before, it shows at once: nothing to wait for, so nothing said.
    again = invoke(application, "set", URL, "--no-rekordbox", pretty=True, columns=100)
    assert again.exit_code == 0 and "listening to the set" not in again.stderr
    started = events(invoke(application, "set", URL, "--no-rekordbox"))[0]
    assert started["identify_reason"] == "no_tracklist"


def test_a_boiler_room_description_is_no_tracklist(
    application, library_http, setup, audio_factory, monkeypatch
):
    """Boiler Room's one line says who played where: djlib listens instead of making it a
    song, and the line never shows up in the set."""
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    setup.page["uploader"] = "Boiler Room"
    setup.page["description"] = (
        "Lumen – live from Boiler Room x Cupra at Primavera Sound Barcelona.\n\n"
        "►  Listen to Boiler Room’s archive on Apple Music: https://apple.co/BoilerRoom\n"
        "►  Subscribe to Boiler Room: https://blrrm.tv/YT\n"
    )
    reply = invoke(application, "set", URL, "--no-rekordbox")
    assert reply.exit_code == 0, reply.output
    assert events(reply)[0]["identify_reason"] == "no_tracklist"
    result = json.loads(reply.stdout)["result"]
    labels = [track["label"] for track in result["tracks"]]
    assert labels == ["Lumen - Halo", "Bicep - Glue", "Nia Okoro - Slow Burn"]
    assert result["identified"]["counts"]["tracks"] == 3


def test_set_listens_when_the_description_names_too_few_songs(
    application, library_http, setup, audio_factory, monkeypatch
):
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    setup.page["description"] = "Lumen - Halo\n"
    reply = invoke(application, "set", URL, "--no-rekordbox", pretty=True, columns=100)
    assert reply.exit_code == 0, reply.output
    said = " ".join(reply.stderr.split())
    assert (
        "The description names only 1 song, so djlib is listening to the set to find the "
        "rest. An hour-long set usually takes 5–10 minutes" in said
    )
    result = json.loads(invoke(application, "set", URL, "--no-rekordbox").stdout)["result"]
    assert result["songs"] == 3 and result["identified"]["reused"] is True
    # Without listening, an hour-long set with two songs listed says songs may be missing.
    setup.page["description"] = "Lumen - Halo\nBicep - Glue\n"
    setup.page["duration_seconds"] = 3431
    envelope = json.loads(
        invoke(application, "set", "https://youtu.be/otherset99", "--no-identify").stdout
    )
    assert envelope["result"]["identified"] is None and envelope["result"]["songs"] == 2
    assert (
        "The description names only 2 songs for this 57-minute set: songs may be missing."
        in envelope["warnings"]
    )
    started = events(invoke(application, "set", "https://youtu.be/otherset98"))[0]
    assert started["identify_reason"] == "sparse"


def test_a_short_list_says_it_came_from_the_chapters(
    application, library_http, setup, audio_factory, monkeypatch
):
    """Three chapters for an hour-long set: the notice and the warnings name the chapters,
    not the description."""
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    setup.page["description"] = "Thanks for listening!"
    setup.page["chapters"] = [
        {"start_time": 0.0, "title": "Lumen - Halo"},
        {"start_time": 150.0, "title": "Bicep - Glue"},
        {"start_time": 240.0, "title": "Nia Okoro - Slow Burn"},
    ]
    setup.page["duration_seconds"] = 3431
    shown = invoke(application, "set", URL, "--no-rekordbox", pretty=True, columns=100)
    assert shown.exit_code == 0, shown.output
    said = " ".join(shown.stderr.split())
    assert "The chapters name only 3 songs, so djlib is listening to the set" in said
    assert "description" not in said
    envelope = json.loads(
        invoke(application, "set", "https://youtu.be/otherset97", "--no-identify").stdout
    )
    assert envelope["result"]["songs"] == 3
    assert envelope["warnings"] == [
        "The chapters name only 3 songs for this 57-minute set: songs may be missing."
    ]

    def missing(name=None):
        raise identification.AppError("DEPENDENCY_REQUIRED", "Song recognition is not installed.")

    monkeypatch.setattr(identification, "recognizer", missing)
    fallback = json.loads(invoke(application, "set", "https://youtu.be/otherset96").stdout)
    assert fallback["warnings"][0] == (
        "djlib didn't listen to the set (song recognition isn't installed), so it uses the "
        "tracklist in its chapters."
    )


def test_a_title_first_description_agrees_with_what_djlib_hears(
    application, library_http, setup, audio_factory, monkeypatch
):
    """Avalon Emerson's description writes "Title - Artist": the songs djlib hears are those
    lines, shown artist first and found in the library, not conflicts."""
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    setup.page["description"] = "Halo - Lumen\nID - Avalon Emerson\nSlow Burn - Nia Okoro\n"
    result = json.loads(invoke(application, "set", URL, "--no-rekordbox").stdout)["result"]
    assert [track["label"] for track in result["tracks"]] == [
        "Lumen - Halo",
        "ID",
        "Nia Okoro - Slow Burn",
    ]
    assert result["identified"]["counts"]["conflict"] == 0
    assert result["owned"] == 1  # Lumen - Halo, as the library tags it
    assert "the description lists it title first" in result["tracks"][0]["notes"]
