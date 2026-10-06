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

    assert [entry["title"] for entry in local.searched] == ["Halo", "Rain", "Snow", "Hail"]
    assert local.searched[0] == {"artist": "Lumen", "title": "Halo", "version": "Dub"}
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
