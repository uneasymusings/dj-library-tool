"""How a tracklist reaches djlib: a file in any common encoding, TextEdit's rich text, what
the DJ copied (``paste``), a pipe (``-``) or a Shazam CSV; and what djlib says when it can't
read one.
"""

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

from djlib.application.tracklists import HEADING, RICH_TEXT, parse_tracklist
from djlib.domain.errors import AppError
from djlib.interfaces import identify_cli, terminal
from djlib.interfaces.library_cli import tracklist_request
from tests.test_headline_workflow import build
from tests.test_identification import URL
from tests.test_identify_cli import fakes, invoke
from tests.test_rekordbox_push import FakeRekordbox

FRIDAY = "Friday — Warm-up\n1. Lumen - Halo\n2. Bicep - Glue\n3. Beyoncé - Cuff It (Wetter Remix)\n"
# What TextEdit saves for FRIDAY (textutil -convert rtf), as it would be on a DJ's Desktop.
FRIDAY_RTF = (
    "{\\rtf1\\ansi\\ansicpg1252\\cocoartf2865\n"
    "\\cocoatextscaling0\\cocoaplatform0{\\fonttbl\\f0\\fswiss\\fcharset0 Helvetica-Light;}\n"
    "{\\colortbl;\\red255\\green255\\blue255;}\n"
    "{\\*\\expandedcolortbl;;}\n"
    "\\pard\\tx560\\tx1120\\tx1680\\tx2240\\pardirnatural\\partightenfactor0\n"
    "\n"
    "\\f0\\fs24 \\cf0 Friday \\'97 Warm-up\\\n"
    "1. Lumen - Halo\\\n"
    "2. Bicep - Glue\\\n"
    "3. Beyonc\\'e9 - Cuff It (Wetter Remix)\\\n"
    "}"
)


def clipboard(monkeypatch, text: str) -> None:
    monkeypatch.setattr(identify_cli, "clipboard", lambda: text.encode("utf-8"))


def owned(library_http, audio_factory):
    build(library_http, [audio_factory("halo.wav", frequency=330)], [("Lumen", "Halo")])


# -- files: encodings, rich text and the errors ------------------------------------------------


@pytest.mark.skipif(sys.platform != "darwin", reason="textutil is macOS's")
def test_a_textedit_rich_text_tracklist_reads_as_its_text(tmp_path):
    rtf = tmp_path / "friday.rtf"
    rtf.write_text(FRIDAY_RTF, encoding="ascii")
    body, warnings = tracklist_request(rtf, None, None)
    assert body.name == "Friday — Warm-up" and warnings == []
    assert [(item.artist, item.title) for item in body.items] == [
        ("Lumen", "Halo"),
        ("Bicep", "Glue"),
        ("Beyoncé", "Cuff It (Wetter Remix)"),
    ]


def test_rich_text_that_cant_be_converted_says_how_to_save_plain_text(tmp_path, monkeypatch):
    rtf = tmp_path / "friday.rtf"
    rtf.write_text(FRIDAY_RTF, encoding="ascii")

    def broken(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "textutil")

    monkeypatch.setattr(identify_cli.subprocess, "run", broken)
    with pytest.raises(AppError) as refused:
        identify_cli.read_text_file(rtf)
    assert refused.value.code == "INPUT_INVALID"
    assert refused.value.message == (
        "friday.rtf is a rich-text file (TextEdit's default). In TextEdit choose Format → Make "
        "Plain Text, save, and run this again — or copy the tracklist and run djlib set paste."
    )
    assert refused.value.details["title"] == "Can't read that tracklist"


def test_rich_text_never_names_the_set():
    items, skipped = parse_tracklist("{\\rtf1\\ansi\\ansicpg1252\\cocoartf2865\nLumen - Halo\n")
    assert len(items) == 1
    assert skipped == [(1, "{\\rtf1\\ansi\\ansicpg1252\\cocoartf2865", RICH_TEXT)]
    # A real heading still names it.
    assert parse_tracklist("Friday at Fabric\nLumen - Halo\n")[1][0][2] == HEADING


@pytest.mark.parametrize(
    "data",
    [
        FRIDAY.encode("cp1252", errors="replace"),  # Windows Notepad, older editors
        FRIDAY.encode("utf-16"),  # with its byte-order mark
        b"\xef\xbb\xbf" + FRIDAY.encode("utf-8"),  # UTF-8 with a BOM
    ],
)
def test_common_encodings_just_work(tmp_path, data):
    path = tmp_path / "friday.txt"
    path.write_bytes(data)
    body, _ = tracklist_request(path, None, None)
    assert [item.artist for item in body.items] == ["Lumen", "Bicep", "Beyoncé"]
    assert body.name.startswith("Friday")


def test_tracklist_file_errors_say_whats_wrong(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cases = {
        Path("nofile.txt"): f"Can't find nofile.txt in {terminal.short_path(str(tmp_path))}.",
        tmp_path: "is a folder; give the tracklist file inside it.",
    }
    for path, words in cases.items():
        with pytest.raises(AppError) as refused:
            identify_cli.read_text_file(path)
        assert refused.value.code == "INPUT_INVALID" and words in refused.value.message, path
        assert refused.value.details["title"] == "Can't read that tracklist"
    (tmp_path / "set.mp3").write_bytes(b"ID3\x04\x00\x00\x00\x00\x00\x00\xff\xfb")
    with pytest.raises(AppError) as refused:
        identify_cli.read_text_file(Path("set.mp3"))
    assert refused.value.message.startswith("djlib can't read set.mp3 as text.")
    # A link typed without https:// gets the whole link back, quoted for zsh.
    with pytest.raises(AppError) as refused:
        identify_cli.read_text_file(Path("youtu.be/abc123"))
    assert refused.value.details["next"] == "djlib set 'https://youtu.be/abc123'"


def test_a_missing_tracklist_shows_a_plain_title(application, tmp_path):
    reply = invoke(application, "set", tmp_path / "nofile.txt", pretty=True)
    assert reply.exit_code == 2
    assert "✗ Can't read that tracklist" in reply.stderr and "Can't find" in reply.stderr
    error = json.loads(invoke(application, "set", tmp_path / "nofile.txt").stdout)["error"]
    assert error["code"] == "INPUT_INVALID"


# -- paste and - -------------------------------------------------------------------------------


def test_paste_with_a_copied_link_checks_that_set(
    application, library_http, audio_factory, monkeypatch
):
    setup = fakes(monkeypatch)
    owned(library_http, audio_factory)
    setup.page["description"] = "Lumen - Halo\nBicep - Glue\n"
    FakeRekordbox(monkeypatch)
    # Share links carry ?si=…, which zsh would refuse unquoted.
    clipboard(monkeypatch, "  https://youtu.be/fridayset1?si=abc123\n")
    reply = invoke(application, "set", "paste", "--no-rekordbox")
    assert reply.exit_code == 0, reply.output
    result = json.loads(reply.stdout)["result"]
    # Used exactly as if typed: trimmed, nothing else.
    assert setup.inspected == ["https://youtu.be/fridayset1?si=abc123"]
    assert (result["name"], result["songs"], result["owned"]) == ("Friday Set", 2, 1)


def test_paste_with_a_copied_tracklist_checks_its_songs(
    application, library_http, audio_factory, monkeypatch
):
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    clipboard(monkeypatch, "Lumen - Halo\nBicep - Glue\nNia Okoro - Slow Burn\n")
    result = json.loads(invoke(application, "set", "paste", "--no-rekordbox").stdout)["result"]
    today = date.today()
    assert result["name"] == f"Pasted tracklist {today.day} {today:%b}"
    assert (result["songs"], result["owned"]) == (3, 1)
    # A heading names it, as in a file.
    clipboard(monkeypatch, "Friday at Fabric\nLumen - Halo\n")
    named = json.loads(invoke(application, "set", "paste", "--no-rekordbox").stdout)["result"]
    assert named["name"] == "Friday at Fabric"
    clipboard(monkeypatch, "see you on the dancefloor\n")
    error = json.loads(invoke(application, "set", "paste").stdout)["error"]
    assert error["code"] == "INPUT_INVALID" and "no set link" in error["message"]
    assert error["details"]["title"] == "No set or tracklist in the clipboard"
    clipboard(monkeypatch, " \n")
    error = json.loads(invoke(application, "set", "paste").stdout)["error"]
    assert error["details"]["title"] == "The clipboard is empty"


def test_a_tracklist_can_be_piped_in(application, library_http, audio_factory, monkeypatch):
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    from typer.testing import CliRunner

    from djlib.interfaces.cli import app as cli_app

    workspace = ["--workspace", str(application.workspace.root)]
    reply = CliRunner().invoke(
        cli_app, [*workspace, "set", "-", "--no-rekordbox"], input="Lumen - Halo\nX - Y\n"
    )
    assert reply.exit_code == 0, reply.output
    result = json.loads(reply.stdout)["result"]
    assert (result["songs"], result["owned"]) == (2, 1)
    created = CliRunner().invoke(
        cli_app, [*workspace, "requests", "create", "--text", "-"], input="Lumen - Halo\n"
    )
    assert json.loads(created.stdout)["result"]["counts"]["satisfied"] == 1


def test_a_file_named_paste_is_still_that_file(
    application, library_http, audio_factory, monkeypatch, tmp_path
):
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "paste").write_text("Lumen - Halo\n", encoding="utf-8")
    monkeypatch.setattr(identify_cli, "clipboard", lambda: pytest.fail("read the clipboard"))
    result = json.loads(invoke(application, "set", "paste", "--no-rekordbox").stdout)["result"]
    assert (result["name"], result["owned"]) == ("paste", 1)


def test_paste_needs_a_mac_and_says_how_to_pipe_instead(monkeypatch):
    monkeypatch.setattr(identify_cli.sys, "platform", "linux")
    with pytest.raises(AppError) as refused:
        identify_cli.read_typed("paste")
    assert refused.value.code == "INPUT_INVALID"
    assert refused.value.message == (
        "djlib set paste reads the clipboard on a Mac; here pipe it in: … | djlib set -"
    )


def test_identify_and_tracklist_take_paste_too(application, monkeypatch, tmp_path):
    clipboard(monkeypatch, "Lumen - Halo\nBicep - Glue\n")
    (found,) = identify_cli.read_tracklists([Path("paste")])
    assert found == {"source": "file", "text": "Lumen - Halo\nBicep - Glue\n"}
    error = json.loads(invoke(application, "identify", "paste").stdout)["error"]
    assert (
        error["code"] == "INPUT_INVALID"
        and "holds a tracklist, not a set link" in (error["message"])
    )
    assert [step["command"] for step in error["details"]["next_steps"]] == [
        "djlib set paste",
        "djlib identify 'LINK' --tracklist paste",
    ]
    clipboard(monkeypatch, URL)
    with pytest.raises(AppError) as refused:
        identify_cli.read_tracklists([Path("paste")])
    assert "that's a link" in refused.value.message


def test_no_argument_shows_the_ways_in(application):
    reply = invoke(application, "set", pretty=True)
    assert reply.exit_code == 2
    lines = [line.strip() for line in reply.stderr.splitlines()]
    assert lines[0].startswith("✗ What should djlib check?")
    since = (date.today() - timedelta(days=30)).isoformat()
    steps = [line for line in lines if line.startswith("→")]
    assert [" ".join(step.split()) for step in steps] == [
        "→ A set link (in single quotes) djlib set 'https://soundcloud.com/…'",
        "→ A tracklist or link you copied djlib set paste",
        f"→ Your recent Shazams djlib set shazam:{since}",
    ]
    error = json.loads(invoke(application, "set").stdout)["error"]
    assert error["code"] == "INPUT_INVALID" and error["details"]["next"].startswith("djlib set '")
    identify = invoke(application, "identify", pretty=True)
    assert identify.exit_code == 2 and "Which set should djlib identify?" in identify.stderr
    assert "djlib identify paste" in identify.stderr


def test_1001tracklists_links_point_to_paste(application, monkeypatch):
    FakeRekordbox(monkeypatch)
    link = "https://www.1001tracklists.com/tracklist/abc/some-set.html"
    reply = invoke(application, "set", link, pretty=True, columns=100)
    assert reply.exit_code == 2
    text = " ".join(reply.stderr.split())
    assert "→ Copy the tracklist on the page, then djlib set paste" in text
    assert "under the player) plus that copy djlib set 'LINK' --tracklist paste" in text
    identify = json.loads(invoke(application, "identify", link).stdout)["error"]
    assert identify["code"] == "SOURCE_BROWSER_ONLY"
    assert identify["details"]["next_steps"][1]["command"] == (
        "djlib identify 'LINK' --tracklist paste"
    )


# -- a Shazam CSV given as the set's file ------------------------------------------------------


def test_a_shazam_csv_is_read_as_your_shazams(
    application, library_http, audio_factory, monkeypatch, tmp_path
):
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    export = tmp_path / "shazamlibrary.csv"
    export.write_text(
        "Shazam Library\nIndex,TagTime,Title,Artist,URL,TrackKey\n"
        "1,2026-10-10,Halo,Lumen,https://www.shazam.com/track/1,1\n"
        "2,2026-10-11,Glue,Bicep,https://www.shazam.com/track/2,2\n",
        encoding="utf-8",
    )
    plain = json.loads(invoke(application, "set", export, "--no-rekordbox").stdout)["result"]
    spec = json.loads(invoke(application, "set", f"shazam:{export}", "--no-rekordbox").stdout)[
        "result"
    ]
    assert plain["request_id"] == spec["request_id"] and plain["source"]["provider"] == "Shazam"
    assert (plain["songs"], plain["owned"]) == (2, 1)
    pretty = invoke(application, "set", export, "--no-rekordbox", pretty=True)
    assert "Read as a Shazam export." in pretty.stderr
    # Title and Artist columns without the Shazam line count too; other CSVs are tracklists.
    columns = tmp_path / "liked.tsv"
    columns.write_text("Artist\tTitle\nLumen\tHalo\n", encoding="utf-8")
    liked = json.loads(invoke(application, "set", columns, "--no-rekordbox").stdout)["result"]
    assert liked["source"]["provider"] == "Shazam" and liked["owned"] == 1
    listing = tmp_path / "friday.csv"
    listing.write_text("Lumen - Halo\nBicep - Glue\n", encoding="utf-8")
    tracklist = json.loads(invoke(application, "set", listing, "--no-rekordbox").stdout)["result"]
    assert tracklist["name"] == "friday" and tracklist["source"] is None
