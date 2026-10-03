"""Authenticated route coverage against the real ASGI app and strict input schemas."""

import httpx
import pytest

from djlib.interfaces.service import create_app


@pytest.fixture
def api(application):
    app = create_app(application.workspace, "instance-test", run_worker=False)
    yield app
    app.state.application.db.engine.dispose()


async def request(api, method, path, application, **kwargs):
    headers = {
        "Authorization": "Bearer " + application.workspace.token(),
        **kwargs.pop("headers", {}),
    }
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(api), base_url="http://127.0.0.1"
    ) as client:
        return await client.request(method, path, headers=headers, **kwargs)


@pytest.mark.parametrize(
    "headers,status,code",
    [({}, 401, "AUTH_REQUIRED"), ({"Authorization": "Bearer bad"}, 401, "AUTH_REQUIRED")],
)
async def test_token_required(api, headers, status, code):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(api), base_url="http://127.0.0.1"
    ) as client:
        reply = await client.get("/health", headers=headers)
    assert reply.status_code == status and reply.json()["error"]["code"] == code


async def test_origin_denied(api, application):
    reply = await request(
        api, "GET", "/health", application, headers={"Origin": "https://evil.test"}
    )
    assert reply.status_code == 403 and reply.json()["error"]["code"] == "ORIGIN_DENIED"


async def test_host_denied(api, application):
    reply = await request(api, "GET", "/health", application, headers={"Host": "evil.test"})
    assert reply.status_code == 400


@pytest.mark.parametrize(
    "route", ["/health", "/capabilities", "/profiles/club", "/jobs", "/library", "/reviews"]
)
async def test_read_routes(api, application, route):
    reply = await request(api, "GET", route, application)
    assert reply.status_code == 200 and reply.json()["ok"]
    assert reply.json()["schema_version"] == "1"


@pytest.mark.parametrize(
    "route,body",
    [
        ("/plans", {"name": "x", "tracks": [], "bad": True}),
        ("/jobs", {"plan_id": "x", "revision": 0, "idempotency_key": "x"}),
        ("/downloads", {"name": "x", "tracks": []}),
        ("/devices/preflight", {"path": "x", "required_bytes": -1}),
    ],
)
async def test_invalid_inputs_are_structured(api, application, route, body):
    reply = await request(api, "POST", route, application, json=body)
    assert reply.status_code == 422 and reply.json()["error"]["code"] == "INPUT_INVALID"


@pytest.mark.parametrize("route", ["/jobs?limit=101", "/library?limit=0", "/reviews?limit=-1"])
async def test_query_limits(api, application, route):
    reply = await request(api, "GET", route, application)
    assert reply.status_code == 422


async def test_plan_start_and_job_routes(api, application, audio_factory):
    body = {
        "name": "HTTP set",
        "tracks": [{"path": str(audio_factory()), "artist": "Artist", "title": "Track"}],
    }
    reply = await request(api, "POST", "/plans", application, json=body)
    plan = reply.json()["result"]
    assert (await request(api, "GET", "/plans/" + plan["plan_id"], application)).json()["ok"]
    reply = await request(
        api,
        "POST",
        "/jobs",
        application,
        json={"plan_id": plan["plan_id"], "revision": 1, "idempotency_key": "http-start"},
    )
    job = reply.json()["result"]
    for suffix in ("", "/items", "/events"):
        assert (await request(api, "GET", "/jobs/" + job["job_id"] + suffix, application)).json()[
            "ok"
        ]
    assert (
        await request(
            api,
            "POST",
            "/jobs/" + job["job_id"] + "/control",
            application,
            json={"action": "pause"},
        )
    ).json()["result"]["state"] == "paused"


async def test_unsupported_provider_actionable(api, application):
    reply = await request(
        api, "POST", "/sources/inspect", application, json={"url": "https://evil.test"}
    )
    assert reply.status_code == 400 and reply.json()["error"]["code"] == "SOURCE_UNSUPPORTED"
