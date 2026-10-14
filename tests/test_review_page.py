"""The local review page: public static files, token-gated data, safe DOM building."""

import json
import re
import shutil
import subprocess
import zipfile
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from djlib.domain.request_contracts import RequestItem
from djlib.interfaces import cli
from djlib.interfaces.client import LocalClient
from djlib.interfaces.service import UI_FILES, UI_HEADERS, create_app
from tests.test_requests import create, named, owned

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
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    workspace = str(application.workspace.root)

    reply = CliRunner().invoke(cli.app, ["--workspace", workspace, "ui"])
    assert reply.exit_code == 0, reply.output
    result = json.loads(reply.stdout)["result"]
    url = result["url"]
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "http://127.0.0.1:4321/ui/"
    handoff = parse_qs(parts.fragment)
    assert handoff["token"] == [application.workspace.token()]
    assert handoff["ws"]  # non-default workspace, so copied commands include it
    assert opened == [] and result["opened"] is False  # JSON output never opens a browser

    pretty = CliRunner(env={"DJLIB_OUTPUT": "pretty"})
    shown = pretty.invoke(cli.app, ["--workspace", workspace, "ui"])
    assert shown.exit_code == 0, shown.output
    assert "Review page" in shown.stdout and opened == [url]
    assert "opened in your browser" in shown.stdout
    quiet = pretty.invoke(cli.app, ["--workspace", workspace, "ui", "--no-open"])
    assert len(opened) == 1 and "opened in your browser" not in quiet.stdout


def test_page_speaks_dj_sets_crates_and_library():
    page = WEB.joinpath("index.html").read_text(encoding="utf-8")
    nav = page.split('<nav class="tabs"', 1)[1].split("</nav>", 1)[0]
    assert re.findall(r">([^<]+)</a>", nav) == ["Sets", "Crates", "Library"]
    assert 'href="#/sets"' in page.split("<nav", 1)[0]  # the brand opens the sets
    assert not re.search("[⠀-⣿]", page)  # the braille mark became an SVG
    assert 'id="version"' not in page

    script = WEB.joinpath("app.js").read_text(encoding="utf-8")
    assert 'const HOME = "#/sets"' in script
    for route in (r"/^#\/sets$/", r"/^#\/sets\/([\w-]+)$/", r"/^#\/requests\/([\w-]+)$/"):
        assert route in script  # old links keep working
    for kept in (r"/^#\/overview$/", r"/^#\/deliveries$/", r"/^#\/jobs$/"):
        assert kept in script
    # The crate hands off to rekordbox, never to the hidden export command.
    assert 'commandText("rekordbox", "push", short)' in script
    assert 'commandText("rekordbox", "usb", short)' in script
    assert 'commandText("export"' not in script
    assert "does not listen to audio" not in script and "revision ${" not in script
    assert 'identify: "Set ID"' in script
    # Store searches open in a new tab without telling the store where they came from.
    assert "https://www.beatport.com/search?q=" in script
    assert 'rel: "noopener noreferrer"' in script
    # The CSP forbids style attributes: anything dynamic goes through the CSSOM.
    assert 'setAttribute("style"' not in script and ".style =" not in script
    assert 'style.setProperty("flex-grow"' in script


def test_page_security_headers_are_unchanged():
    assert UI_HEADERS["Content-Security-Policy"] == (
        "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
        "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    )


# The page's pure helpers, run in node's vm without a DOM (start() is left out).
HARNESS = r"""
const vm = require("vm");
const fs = require("fs");
const source = fs.readFileSync(process.argv[1], "utf8").replace(/\nstart\(\);\s*$/, "\n");
const context = vm.createContext({ URL, URLSearchParams });
vm.runInContext(source, context);
const out = JSON.parse(process.argv[2]).map((expression) => vm.runInContext(expression, context));
process.stdout.write(JSON.stringify(out));
"""


def run_page(*expressions):
    reply = subprocess.run(
        ["node", "-e", HARNESS, str(WEB.joinpath("app.js")), json.dumps(expressions)],
        capture_output=True,
        text=True,
        timeout=60,
        check=True,
    )
    return json.loads(reply.stdout)


needs_node = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")


@needs_node
def test_page_short_ids_match_the_terminal():
    from djlib.interfaces.terminal import short_id

    ids = [
        "collection_5d7cbba933984f0f9fac25d9c9eacc20",
        "request_5afa55584558455085a8322ed3bd469b",
    ]
    shown = run_page(*(f"shortId({json.dumps(value)})" for value in ids))
    assert shown == [short_id(value) for value in ids]
    assert shown[0] == ids[0][11:19]


@needs_node
def test_page_links_only_to_the_set_and_store_searches():
    youtube = "https://www.youtube.com/watch?v=cmgN8X7Pp_0"
    soundcloud = "https://soundcloud.com/platform/kaytranada-boiler-room"
    links = run_page(
        f'listenUrl("{youtube}", stampSeconds("05:39"))',
        'listenUrl("https://youtu.be/cmgN8X7Pp_0", stampSeconds("1:02:09"))',
        f'listenUrl("{soundcloud}", 339)',
        f'listenUrl("{soundcloud}", 3729)',
        'listenUrl("http://www.youtube.com/watch?v=cmgN8X7Pp_0", 10)',
        'listenUrl("https://www.youtube.com.evil.example/watch?v=cmgN8X7Pp_0", 10)',
        'listenUrl("https://www.mixcloud.com/someone/a-set/", 10)',
        'listenUrl("javascript:alert(1)", 10)',
        'listenUrl("https://user@soundcloud.com/a/b", 10)',
        'provider("https://m.youtube.com/watch?v=cmgN8X7Pp_0")',
        'provider("https://www.1001tracklists.com/tracklist/x.html")',
    )
    assert links == [
        "https://www.youtube.com/watch?v=cmgN8X7Pp_0&t=339s",
        "https://www.youtube.com/watch?v=cmgN8X7Pp_0&t=3729s",
        f"{soundcloud}#t=5:39",
        f"{soundcloud}#t=1:02:09",
        None,
        None,
        None,
        None,
        None,
        "youtube",
        None,
    ]
    song = {"artist": "Antdot ft. Rona Ray", "title": "Inner Wars", "version": "Original Mix"}
    shop = {"beatport": "https://evil.example/", "bandcamp": "https://bandcamp.com/search?q=x"}
    built, given = run_page(
        f"storeLinks({json.dumps({'input': song})})",
        f"storeLinks({json.dumps({'input': song, 'shop': shop})})",
    )
    assert built == [
        ["Beatport", "https://www.beatport.com/search?q=Antdot%20Inner%20Wars"],
        ["Bandcamp", "https://bandcamp.com/search?q=Antdot%20Inner%20Wars&item_type=t"],
    ]
    assert given == [built[0], ["Bandcamp", "https://bandcamp.com/search?q=x"]]


@needs_node
def test_page_reads_an_ids_lead_out_of_its_notes():
    maybe, likely, plain = run_page(
        'idHint("Listed as an ID in the description; possibly Chris Fortier & Neil Kolo - All I '
        "Got (Chris Fortier 20yr Dub) (Shazam, 1 window at 50:09); possibly BR1S4 - THIS IS "
        'BRAZIL (Shazam, 1 window at 51:09)")',
        'idHint("ID (likely Antdot - Mirage): Shazam heard it in 2 windows from 05:39")',
        'idHint("Listed as an ID in the description")',
    )
    assert maybe == {
        "hint": "maybe Chris Fortier & Neil Kolo - All I Got (Chris Fortier 20yr Dub)",
        "rest": "Listed as an ID in the description; possibly BR1S4 - THIS IS BRAZIL "
        "(Shazam, 1 window at 51:09)",
    }
    assert likely == {
        "hint": "likely Antdot - Mirage",
        "rest": "Shazam heard it in 2 windows from 05:39",
    }
    assert plain == {"hint": None, "rest": "Listed as an ID in the description"}


async def test_saved_sets_carry_the_tally_the_page_draws(application, audio_factory, http):
    await owned(application, audio_factory, name="mirage", title="Mirage")
    radio = {"title": "Poison", "version": "Radio Edit", "frequency": 550}
    await owned(application, audio_factory, name="poison", **radio)
    ledger = create(
        application,
        [
            named(title="Mirage"),
            named(title="Poison", version="Extended Mix"),
            named(title="Not here"),
            RequestItem(kind="unknown", label="ID", timestamp="48:30"),
        ],
    )
    token = {"Authorization": "Bearer " + application.workspace.token()}
    row = http.get("/requests", headers=token).json()["result"]["requests"][0]
    assert row["request_id"] == ledger["request_id"]
    assert row["counts"] == {"owned": 1, "missing": 1, "other_mix": 1, "ids": 1}
    assert "items" not in row
    if shutil.which("node"):
        # The page's own count of the same items agrees with the service.
        items = http.get(f"/requests/{ledger['request_id']}", headers=token).json()["result"]
        assert run_page(f"countsOf({json.dumps(items['items'])})") == [row["counts"]]


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
