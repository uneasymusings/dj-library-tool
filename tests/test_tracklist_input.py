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
from tests.test_identify_cli import LISTED, fakes, invoke
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
    assert result["name"] == f"Pasted {today.day} {today:%b} · Lumen - Halo + 2 more"
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
    assert refused.value.details["title"] == "Copy the tracklist too"


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
    assert "(under the player) djlib set 'LINK' --tracklist paste" in text
    identify = json.loads(invoke(application, "identify", link).stdout)["error"]
    assert identify["code"] == "SOURCE_BROWSER_ONLY"
    # Listening needs the set's own link, so for identify that way comes first.
    assert identify["details"]["next_steps"][0]["command"] == (
        "djlib identify 'LINK' --tracklist paste"
    )
    # Copied from the address bar: the same way out, never a fetch of the page.
    clipboard(monkeypatch, link)
    for command, first in (
        ("set", "djlib set paste"),
        ("identify", "djlib identify 'LINK' --tracklist paste"),
    ):
        error = json.loads(invoke(application, command, "paste").stdout)["error"]
        assert error["code"] == "SOURCE_BROWSER_ONLY", command
        assert error["details"]["next"] == first


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
    # Shazam's own TagTime column is enough without the "Shazam Library" line.
    tagged = tmp_path / "tagged.tsv"
    tagged.write_text("TagTime\tArtist\tTitle\n2026-10-10\tLumen\tHalo\n", encoding="utf-8")
    shazams = json.loads(invoke(application, "set", tagged, "--no-rekordbox").stdout)["result"]
    assert shazams["source"]["provider"] == "Shazam" and shazams["owned"] == 1
    # A plain .csv tracklist, without columns, is still a tracklist.
    listing = tmp_path / "friday.csv"
    listing.write_text("Lumen - Halo\nBicep - Glue\n", encoding="utf-8")
    tracklist = json.loads(invoke(application, "set", listing, "--no-rekordbox").stdout)["result"]
    assert tracklist["name"] == "friday" and tracklist["source"] is None


def test_a_spreadsheet_csv_is_a_song_list_named_after_its_file(
    application, library_http, audio_factory, monkeypatch, tmp_path
):
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    sheet = tmp_path / "Warehouse Friday.csv"
    sheet.write_text(
        "#,Artist,Title,Mix,Label\n1,Lumen,Halo,,Lumen Rec\n2,Bicep,Glue,Extended Mix,Ninja\n"
        "3,Nia Okoro,Slow Burn (Dub),,\n",
        encoding="utf-8",
    )
    pretty = invoke(application, "set", sheet, "--no-rekordbox", pretty=True)
    assert "Read as a song list (Artist and Title columns)." in pretty.stderr
    assert "Shazam" not in pretty.stdout + pretty.stderr
    result = json.loads(invoke(application, "set", sheet, "--no-rekordbox").stdout)["result"]
    # Not your Shazams: named after the file, so it never takes the Shazam set's playlist.
    assert result["name"] == "Warehouse Friday" and result["source"] is None
    assert "shazam" not in result and (result["songs"], result["owned"]) == (3, 1)
    # In the sheet's order, with the Mix column (or a mix in the title) kept exact.
    assert [item["label"] for item in result["missing"]] == [
        "Bicep - Glue (Extended Mix)",
        "Nia Okoro - Slow Burn (Dub)",
    ]
    mixes = tmp_path / "mixes.tsv"
    mixes.write_text("Artist\tTitle\tVersion\nLumen\tHalo\tExtended Mix\n", encoding="utf-8")
    other = json.loads(invoke(application, "set", mixes, "--no-rekordbox").stdout)["result"]
    # Only the original Halo is owned: a different mix is never swapped in.
    assert other["owned"] == 0 and other["missing"][0]["label"] == "Lumen - Halo (Extended Mix)"


# -- each paste is its own set -----------------------------------------------------------------


def test_different_pastes_get_their_own_playlists(
    application, library_http, audio_factory, monkeypatch
):
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    today = date.today()

    def paste(text: str) -> dict:
        clipboard(monkeypatch, text)
        reply = invoke(application, "set", "paste")
        assert reply.exit_code == 0, reply.output
        return json.loads(reply.stdout)["result"]

    first = paste("Bicep - Glue\nLumen - Halo\n")
    second = paste("Nia Okoro - Slow Burn\nLumen - Halo\n")
    # DJs reuse an opener: the same first song in a longer set is another set too.
    third = paste("Bicep - Glue\nLumen - Halo\nNia Okoro - Slow Burn\n")
    day = f"{today.day} {today:%b}"
    assert first["name"] == f"Pasted {day} · Bicep - Glue + 1 more"
    assert second["name"] == f"Pasted {day} · Nia Okoro - Slow Burn + 1 more"
    assert third["name"] == f"Pasted {day} · Bicep - Glue + 2 more"
    # A different set is never called a newer version of another one.
    assert [run["replaces"] for run in (first, second, third)] == [None, None, None]
    assert len({run["playlist"] for run in (first, second, third)}) == 3
    # The same paste again is the same set.
    assert paste("Bicep - Glue\nLumen - Halo\n")["request_id"] == first["request_id"]


def test_cells_copied_from_a_spreadsheet_read_as_a_song_list(
    application, library_http, audio_factory, monkeypatch
):
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    clipboard(monkeypatch, "Artist\tTitle\nLumen\tHalo\nBicep\tGlue - Original Mix\n")
    pretty = invoke(application, "set", "paste", "--no-rekordbox", pretty=True)
    assert "Read as a song list (Artist and Title columns)." in pretty.stderr
    result = json.loads(invoke(application, "set", "paste", "--no-rekordbox").stdout)["result"]
    today = date.today()
    assert result["name"] == f"Pasted {today.day} {today:%b} · Lumen - Halo + 1 more"
    assert result["owned"] == 1
    assert [item["label"] for item in result["missing"]] == ["Bicep - Glue (Original Mix)"]
    # Without the header row djlib can't tell artist from title, and says so.
    clipboard(monkeypatch, "Lumen\tHalo\nBicep\tGlue\n")
    error = json.loads(invoke(application, "set", "paste").stdout)["error"]
    assert error["message"].endswith(
        "From a spreadsheet? Copy its Artist and Title header row too."
    )


# -- links: with other words, with quotes, without https:// ------------------------------------


def test_a_copied_link_among_other_words_is_still_the_link(
    application, library_http, audio_factory, monkeypatch
):
    setup = fakes(monkeypatch)
    owned(library_http, audio_factory)
    setup.page["description"] = "Lumen - Halo\nBicep - Glue\n"
    FakeRekordbox(monkeypatch)
    clipboard(monkeypatch, "check this set later!! https://youtu.be/fridayset1.\n")
    reply = invoke(application, "set", "paste", "--no-rekordbox", pretty=True)
    assert reply.exit_code == 0, reply.output
    assert "Using the link you copied: https://youtu.be/fridayset1\n" in reply.stderr
    assert setup.inspected == ["https://youtu.be/fridayset1"]
    # Two links: djlib asks for the one set, in set and in identify.
    clipboard(monkeypatch, "https://youtu.be/one and https://youtu.be/two")
    for command in ("set", "identify"):
        error = json.loads(invoke(application, command, "paste").stdout)["error"]
        assert error["details"]["title"] == "Copy just the set's link", command
    # Words with no link and no song: identify doesn't call them a tracklist.
    clipboard(monkeypatch, "see you on the dancefloor")
    error = json.loads(invoke(application, "identify", "paste").stdout)["error"]
    assert error["details"]["title"] == "No set link in the clipboard"


def test_identify_paste_listens_to_the_copied_link(application, library_http, monkeypatch):
    setup = fakes(monkeypatch)
    clipboard(monkeypatch, URL)
    reply = invoke(application, "identify", "paste", pretty=True)
    assert reply.exit_code == 0, reply.output
    assert f"Using the link you copied: {URL}" in reply.stderr
    assert setup.fetched == [URL]


def test_tracklist_paste_with_the_link_still_copied_asks_for_the_tracklist(
    application, monkeypatch
):
    FakeRekordbox(monkeypatch)
    clipboard(monkeypatch, URL)
    for arguments in (
        ["set", URL, "--tracklist", "paste", "--no-rekordbox"],
        ["requests", "create", "--text", "paste"],
    ):
        reply = invoke(application, *arguments, pretty=True)
        assert reply.exit_code == 2, arguments
        # No "Using the link you copied" first: the link isn't what this needs.
        assert reply.stderr.startswith("✗ Copy the tracklist too"), arguments
        assert "The clipboard still holds the set's link." in reply.stderr


@pytest.mark.parametrize("quotes", ["'{}'", '"{}"', "‘{}’", "“{}”"])
def test_a_link_that_arrives_with_its_quotes_is_still_the_link(
    application, library_http, audio_factory, monkeypatch, quotes
):
    """cmd.exe passes single quotes through; Notes and Messages curl them."""
    setup = fakes(monkeypatch)
    owned(library_http, audio_factory)
    setup.page["description"] = "Lumen - Halo\n"
    FakeRekordbox(monkeypatch)
    link = "https://youtu.be/fridayset1?si=abc123"
    reply = invoke(application, "set", quotes.format(link), "--no-rekordbox")
    assert reply.exit_code == 0, reply.output
    assert setup.inspected == [link]


def test_files_and_bare_links_are_named_as_typed(application, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    error = json.loads(invoke(application, "set", "‘Friday set.txt’").stdout)["error"]
    assert error["message"].startswith("Can't find Friday set.txt in ")
    bare = "soundcloud.com/platform/kaytranada-boiler-room"
    for command, typed in (("set", f"'{bare}'"), ("identify", bare)):
        error = json.loads(invoke(application, command, typed).stdout)["error"]
        assert error["details"]["title"] == "That link needs its https://", command
        assert error["details"]["next"] == f"djlib {command} 'https://{bare}'"
    # A file whose name has quotes in it is still that file.
    (tmp_path / "'odd'.txt").write_text("Lumen - Halo\n", encoding="utf-8")
    assert identify_cli.unquoted("'odd'.txt") == "'odd'.txt"
    assert identify_cli.unquoted("“https://youtu.be/x”") == "https://youtu.be/x"


def test_an_empty_tracklist_file_says_its_empty(application, tmp_path):
    empty = tmp_path / "empty.txt"
    empty.write_bytes(b"")
    error = json.loads(invoke(application, "set", empty).stdout)["error"]
    assert error["message"] == (
        "empty.txt is empty; paste the set's tracklist into it, or copy it and run djlib set paste."
    )
    assert error["details"]["title"] == "Can't read that tracklist"


# -- paste and - with the set's link -----------------------------------------------------------


def test_a_tracklist_for_the_set_can_be_piped_in(
    application, library_http, audio_factory, monkeypatch
):
    fakes(monkeypatch)
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    from typer.testing import CliRunner

    from djlib.interfaces.cli import app as cli_app

    workspace = ["--workspace", str(application.workspace.root)]
    reply = CliRunner().invoke(
        cli_app,
        [*workspace, "set", URL, "--tracklist", "-", "--no-rekordbox"],
        input="1001Tracklists\n" + LISTED,
    )
    assert reply.exit_code == 0, reply.output
    result = json.loads(reply.stdout)["result"]
    assert result["identified"]["counts"]["likely"] == 1 and result["songs"] == 3
    # The link piped in where its tracklist should be.
    linked = CliRunner().invoke(
        cli_app, [*workspace, "set", URL, "--tracklist", "-", "--no-rekordbox"], input=URL
    )
    error = json.loads(linked.stdout)["error"]
    assert error["code"] == "INPUT_INVALID" and error["details"]["title"] == "That's the set's link"


def test_a_pasted_tracklist_with_its_link_reaches_the_listening(
    application, library_http, audio_factory, monkeypatch
):
    fakes(monkeypatch)
    owned(library_http, audio_factory)
    FakeRekordbox(monkeypatch)
    sent = []
    submit = identify_cli.submit

    def spy(local, url, **options):
        sent.append((url, options["tracklists"]))
        return submit(local, url, **options)

    monkeypatch.setattr(identify_cli, "submit", spy)
    copied = "1001Tracklists\n" + LISTED
    clipboard(monkeypatch, copied)
    reply = invoke(application, "set", "paste", "--source", URL, "--identify", "--no-rekordbox")
    assert reply.exit_code == 0, reply.output
    # The copied text itself, as a file's would be.
    assert sent == [(URL, [{"source": "1001tracklists", "text": copied}])]
    result = json.loads(reply.stdout)["result"]
    assert result["identified"]["counts"]["likely"] == 1 and result["songs"] == 3


# -- how next steps and init read --------------------------------------------------------------


@pytest.mark.parametrize("width", [50, 80, 140])
def test_next_steps_line_up_only_when_every_one_fits(capsys, width):
    from tests.test_terminal import sized

    terminal.render_error(sized(width), identify_cli.nothing_given("set").as_dict())
    lines = capsys.readouterr().err.splitlines()
    steps = [line for line in lines if line.lstrip().startswith("→")]
    assert len(steps) == 3 and max(len(line) for line in lines) <= width
    if width < 80:
        # Too narrow for all three: every command goes under its label, none beside one.
        assert not any("djlib" in step for step in steps)
        assert len([line for line in lines if line.startswith("    djlib ")]) == 3
    else:
        assert len({step.index("djlib") for step in steps}) == 1


def test_a_long_label_puts_every_command_under_its_label(capsys):
    from djlib.interfaces.set_sources import browser_only
    from tests.test_terminal import sized

    with pytest.raises(AppError) as refused:
        browser_only("https://www.1001tracklists.com/tracklist/abc/x.html")
    terminal.render_error(sized(140), refused.value.as_dict())
    lines = capsys.readouterr().err.splitlines()
    # No short label pushed far right by the long one.
    assert "  → Copy the tracklist on the page, then" in lines
    assert "    djlib set paste" in lines and "    djlib set 'LINK' --tracklist paste" in lines
    assert not any(" " * 6 in line.strip() for line in lines)


def test_init_puts_a_long_new_folder_on_its_own_line(capsys):
    from djlib.interfaces import views
    from tests.test_terminal import sized

    folder = "/Volumes/DJ Drive/Music/Promos 2026/October/Labels I Play Out/Warehouse"
    result = {"created": False, "added": [folder], "config": {"allowed_roots": [folder]}}
    views.init_view(sized(80), result)
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].rstrip() == "✓ Added to your music folders"
    assert lines[1].strip() == folder and max(len(line) for line in lines) <= 80
    assert lines[-1].split() == ["→", "Index", "the", "new", "music", "djlib", "scan"]
