import pytest

from djlib.domain.errors import AppError
from djlib.interfaces import set_sources


def test_timestamps_and_set_urls():
    assert set_sources.seconds("58:30") == 3510
    assert set_sources.seconds("1:02:03") == 3723
    assert set_sources.seconds("soon") is None and set_sources.seconds(None) is None
    set_sources.check_set_url("https://soundcloud.com/matzo/fabric-mix-2026")
    set_sources.check_set_url("https://m.youtube.com/watch?v=x")
    with pytest.raises(AppError) as blocked:
        set_sources.check_set_url("https://www.1001tracklists.com/tracklist/x/set.html")
    assert blocked.value.code == "SOURCE_BROWSER_ONLY" and "browser" in blocked.value.message
    with pytest.raises(AppError):
        set_sources.check_set_url("https://example.com/set")


class FakeLocal:
    def __init__(self, answers):
        self.answers, self.searched = answers, []

    def request(self, method, path, *, data=None, params=None):
        assert (method, path) == ("POST", "/sources/search")
        self.searched.append(data)
        answer = self.answers[data["title"]]
        if isinstance(answer, AppError):
            raise answer
        return {"result": {"candidates": answer}}


def item(position, title, state="missing", kind="named", version=""):
    return {
        "position": position,
        "state": state,
        "input": {"kind": kind, "artist": "Lumen", "title": title, "version": version},
    }


def test_fetch_plans_only_confident_matches_for_missing_songs():
    clear = {
        "provider": "youtube",
        "url": "https://youtu.be/a",
        "title": "Lumen - Halo",
        "uploader": "Lumen",
        "duration": 200.0,
        "score": 0.9,
        "reasons": [],
        "confident": True,
    }
    vague = {**clear, "url": "https://youtu.be/b", "confident": False, "score": 0.5}
    local = FakeLocal(
        {"Halo": [clear], "Rain": [vague], "Snow": [], "Hail": AppError("SOURCE_RATE_LIMITED", "x")}
    )
    items = [
        item(1, "Halo", version="Dub"),
        item(2, "Rain"),
        item(3, "Snow"),
        item(4, "Hail"),
        item(5, "Owned", state="satisfied"),
        item(6, "ID", kind="unknown"),
    ]

    chosen, undecided = set_sources.plan_fetch(local, items)

    # Searches run a few at a time; only missing named songs are searched.
    assert sorted(entry["title"] for entry in local.searched) == ["Hail", "Halo", "Rain", "Snow"]
    assert {"artist": "Lumen", "title": "Halo", "version": "Dub"} in local.searched
    assert [(c["label"], c["title"], c["source"]["url"]) for c in chosen] == [
        ("Lumen - Halo (Dub)", "Halo", "https://youtu.be/a")
    ]
    assert [(u["label"], u["reason"]) for u in undecided] == [
        ("Lumen - Rain", "no confident match"),
        ("Lumen - Snow", "nothing found"),
        ("Lumen - Hail", "SOURCE_RATE_LIMITED"),
    ]
    assert undecided[0]["options"][0]["url"] == "https://youtu.be/b"

    body = set_sources.download_body("Friday", "request_1", chosen)
    assert body["name"] == "Friday — downloads"
    assert body["tracks"] == [
        {"url": "https://youtu.be/a", "artist": "Lumen", "title": "Halo", "version": "Dub"}
    ]
    assert (
        body["idempotency_key"]
        == set_sources.download_body("Friday", "request_1", chosen)["idempotency_key"]
    )


def test_named_in_comments_points_to_credited_mentions_first():
    comments = [
        {"text": "2:41 - gunk / 12:00 - freedom 2 / 38:00 - hackney parrot"},
        {"text": "this is The Streets - Turn the page (Overmono remix)"},
    ]
    message = set_sources.named_in_comments(comments)
    assert message.startswith(" Listeners named 4 tracks in the comments, e.g. The Streets")
    assert set_sources.named_in_comments([]) == ""
    assert set_sources.named_in_comments([{"text": "ID?"}]) == ""


def test_search_queries_leave_out_original_mix_and_country_tags(library_http, monkeypatch):
    from djlib.sources import web

    queries = []

    async def search(provider, query, limit=8):
        queries.append(query)
        return []

    monkeypatch.setattr(web, "search", search)
    library_http.post(
        "/sources/search",
        json={"artist": "Antdot & Maz (BR)", "title": "Lasso (Original Mix)", "version": ""},
    )
    assert queries == ["Antdot & Maz Lasso", "Antdot & Maz Lasso"]


def test_search_checks_the_best_uploads_and_drops_gone_ones(library_http, monkeypatch):
    """The live Phoenix - Lasso case: the official SoundCloud upload became DRM-only."""
    from djlib.sources import web
    from tests.test_source_matching import GLASSNOTE_LASSO, OFFICIAL_LASSO, lasso_search

    async def search(provider, query, limit=8):
        return [entry for entry in lasso_search() if entry["provider"] == provider]

    probed = []

    async def probe(url):
        probed.append(url)
        if url == OFFICIAL_LASSO:
            raise web.provider_error(b"ERROR: [soundcloud] 1: This video is DRM protected")
        return {"url": url, "duration": 167.9}

    monkeypatch.setattr(web, "search", search)
    monkeypatch.setattr(web, "probe", probe)
    reply = library_http.post(
        "/sources/search", json={"artist": "Phoenix", "title": "Lasso (Original Mix)"}
    ).json()["result"]

    # The best three of the requested version are checked; remixes and live takes are not.
    assert len(probed) == 3 and OFFICIAL_LASSO in probed and GLASSNOTE_LASSO in probed
    best = reply["candidates"][0]
    assert (best["url"], best["confident"], best["available"]) == (GLASSNOTE_LASSO, True, True)
    assert OFFICIAL_LASSO not in [item["url"] for item in reply["candidates"]]
    [gone] = reply["unavailable"]
    assert gone["url"] == OFFICIAL_LASSO and gone["uploader"] == "Phoenix"
    assert "DRM" in gone["reason"]


def test_sources_without_a_tracklist_explain_and_list_what_listeners_named(
    application, library_http, monkeypatch
):
    import json

    from typer.testing import CliRunner

    import djlib.interfaces.service as service_module
    from djlib.interfaces.cli import app as cli_app
    from tests.test_rekordbox_push import FakeRekordbox

    comments = [
        {"text": "2:41 - gunk / 12:00 - freedom 2 / 38:00 - hackney parrot", "start_time": None},
        {"text": "this is The Streets - Turn the page (Overmono remix) 26:06", "start_time": None},
    ]

    async def inspect(url, count=0):
        return {
            "url": url,
            "title": "Boiler Room",
            "description": "",
            "chapters": [],
            "comments": comments if count else [],
        }

    monkeypatch.setattr(service_module, "inspect_source", inspect)
    inspected = library_http.post(
        "/sources/inspect", json={"url": "https://youtu.be/x", "comments": 100}
    ).json()["result"]
    assert [hint["at"] for hint in inspected["named_in_comments"]] == [
        "2:41",
        "12:00",
        "26:06",
        "38:00",
    ]
    FakeRekordbox(monkeypatch)
    reply = CliRunner().invoke(
        cli_app, ["--workspace", str(application.workspace.root), "set", "https://youtu.be/x"]
    )
    error = json.loads(reply.stdout)["error"]
    assert error["code"] == "TRACKLIST_NOT_FOUND"
    labels = [hint["label"] for hint in error["details"]["named_in_comments"]]
    assert "The Streets - Turn the page (Overmono remix)" in labels and labels[0] == "gunk"
