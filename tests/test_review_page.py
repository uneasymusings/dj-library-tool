"""The local review page: public static files, token-gated data, safe DOM building."""

import json
import shutil
import subprocess
import zipfile
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from djlib.interfaces import cli
from djlib.interfaces.client import LocalClient
from djlib.interfaces.service import UI_FILES, UI_HEADERS, create_app

WEB = files("djlib.interfaces").joinpath("web")


@pytest.fixture
def http(application):
    with TestClient(
        create_app(application.workspace, "review-page-test", run_worker=False),
        base_url="http://127.0.0.1",
    ) as client:
        yield client


@pytest.mark.parametrize("path", sorted(UI_FILES))
def test_page_files_load_without_the_token_and_with_strict_headers(http, path):
    response = http.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"] == UI_FILES[path][1]
    for name, value in UI_HEADERS.items():
        assert response.headers[name] == value
    assert "'unsafe-inline'" not in response.headers["content-security-policy"]


def test_data_routes_still_require_the_token(http, application):
    for path in ("/library", "/jobs", "/requests", "/deliveries", "/capabilities"):
        reply = http.get(path)
        assert reply.status_code == 401
        assert reply.json()["error"]["code"] == "AUTH_REQUIRED"
    # Only exact GET paths are public; other methods and neighbours are not data routes.
    assert http.post("/ui/").status_code == 405
    assert http.get("/ui/missing.js").status_code == 404
    token = {"Authorization": "Bearer " + application.workspace.token()}
    same_origin = {**token, "Origin": "http://127.0.0.1"}
    assert http.get("/library", headers=same_origin).status_code == 200
    foreign = {**token, "Origin": "http://evil.example"}
    assert http.get("/library", headers=foreign).json()["error"]["code"] == "ORIGIN_DENIED"


def test_page_builds_dom_without_html_injection_sinks():
    script = WEB.joinpath("app.js").read_text(encoding="utf-8")
    for sink in (
        "innerHTML",
        "outerHTML",
        "insertAdjacentHTML",
        "document.write",
        "eval(",
        "new Function",
    ):
        assert sink not in script
    page = WEB.joinpath("index.html").read_text(encoding="utf-8")
    assert "<script>" not in page and "style=" not in page  # CSP forbids inline code


def test_ui_command_returns_a_token_link_and_only_opens_in_a_terminal(application, monkeypatch):
    opened = []
    monkeypatch.setattr(LocalClient, "start", lambda self: "http://127.0.0.1:4321")
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    workspace = str(application.workspace.root)

    reply = CliRunner().invoke(cli.app, ["--workspace", workspace, "ui"])
    assert reply.exit_code == 0, reply.output
    url = json.loads(reply.stdout)["result"]["url"]
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "http://127.0.0.1:4321/ui/"
    handoff = parse_qs(parts.fragment)
    assert handoff["token"] == [application.workspace.token()]
    assert handoff["ws"]  # non-default workspace, so copied commands include it
    assert opened == []  # JSON output never opens a browser

    pretty = CliRunner(env={"DJLIB_OUTPUT": "pretty"})
    shown = pretty.invoke(cli.app, ["--workspace", workspace, "ui"])
    assert shown.exit_code == 0, shown.output
    assert "Review page" in shown.stdout and opened == [url]
    pretty.invoke(cli.app, ["--workspace", workspace, "ui", "--no-open"])
    assert len(opened) == 1


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs uv to build the wheel")
def test_wheel_ships_the_page(tmp_path):
    project = Path(__file__).resolve().parents[1]
    subprocess.run(
        ["uv", "build", "--wheel", "--quiet", "--out-dir", str(tmp_path)],
        cwd=project,
        check=True,
        timeout=300,
    )
    wheel = next(tmp_path.glob("*.whl"))
    names = set(zipfile.ZipFile(wheel).namelist())
    for name in ("index.html", "app.js", "app.css"):
        assert f"djlib/interfaces/web/{name}" in names
