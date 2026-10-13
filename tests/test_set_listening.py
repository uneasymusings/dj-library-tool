"""`djlib set LINK` listens to a set when its tracklist is missing or has IDs.

Everything runs through the real coordinator; the page, the download and the recognizer are
the fakes of tests/test_identification.py.
"""

import json

import pytest

from djlib.application import identification
from tests.test_headline_workflow import build
from tests.test_identification import URL, Setup
from tests.test_identify_cli import LISTED, fakes, invoke
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
    # An ID is not a named song, even a likely one.
    assert identified["coverage"]["percent"] == 67
    assert identified["tracklist_file"].endswith("tracklist.txt") and identified["job_id"]
    missing = {item["position"]: item for item in result["missing"]}
    assert missing[2]["label"].startswith("ID @ ")
    assert missing[2]["notes"].startswith("ID (likely Bicep - Glue)")
    assert fake.imported[-1].name == "Friday Set.m3u8"
    assert identification.UNOFFICIAL_SHAZAM in envelope["warnings"]
    assert len(setup.inspected) == 1  # the identification reused the page djlib set read

    # Once identified, the same set reuses what djlib heard.
    again = json.loads(invoke(application, "set", URL).stdout)["result"]
    assert again["identified"]["reused"] is True and len(setup.fetched) == 1


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
    assert error["code"] == "TRACKLIST_NOT_FOUND" and "leave out --no-identify" in error["message"]


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
        "djlib didn't listen to the set: song recognition isn't installed; install it with: "
        f"{identification.INSTALLER}. This uses the tracklist in its description."
    )
    explicit = json.loads(invoke(application, "set", URL, "--identify").stdout)["error"]
    assert explicit["code"] == "DEPENDENCY_REQUIRED"
    fakes.page["description"] = ""
    missing = json.loads(invoke(application, "set", URL).stdout)["error"]
    assert missing["code"] == "TRACKLIST_NOT_FOUND"
    assert missing["message"].endswith(
        "djlib could listen to the set and find its songs itself, but song recognition isn't "
        f"installed; install it with: {identification.INSTALLER}"
    )


def test_set_merges_a_1001tracklists_file_with_what_it_hears(
    application, library_http, setup, audio_factory, monkeypatch, tmp_path
):
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    listing = tmp_path / "tracklist.txt"
    listing.write_text("1001Tracklists\n" + LISTED, encoding="utf-8")
    # A link with the file to merge…
    result = json.loads(invoke(application, "set", URL, "--tracklist", listing).stdout)["result"]
    assert result["identified"]["counts"]["likely"] == 1 and result["songs"] == 3
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
