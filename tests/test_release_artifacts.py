"""Release identity, standalone skill contents, and integrity manifests."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from zipfile import ZipFile

import pytest

from djlib import __version__
from scripts.release_artifacts import project_version, release_assets


def test_release_version_matches_engine():
    root = Path(__file__).resolve().parent.parent
    assert project_version(root) == __version__
    packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    locked_project = next(package for package in packages if package["name"] == "dj-library-tool")
    assert locked_project["version"] == __version__


def test_plugin_version_matches_package():
    root = Path(__file__).resolve().parent.parent
    plugin = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
    assert plugin["version"] == project_version(root) == __version__
    assert plugin["mcpServers"]["djlib"] == {"command": "djlib", "args": ["mcp", "serve"]}
    marketplace = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())
    (entry,) = marketplace["plugins"]
    assert entry["name"] == plugin["name"] == "djlib"
    assert entry["source"] == "./"
    assert (root / "skills" / "dj-library" / "SKILL.md").is_file()


def test_release_refuses_a_stale_plugin_version(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.2.2"}')
    with pytest.raises(SystemExit, match="1.2.2.*1.2.3"):
        release_assets(tmp_path)


def test_skill_archive_is_complete_reproducible_and_allowlisted(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    (tmp_path / "LICENSE").write_text("Test license")
    (tmp_path / ".claude-plugin").mkdir()
    (tmp_path / ".claude-plugin" / "plugin.json").write_text('{"version": "1.2.3"}')
    skill = tmp_path / "skills" / "dj-library"
    (skill / "references" / "__pycache__").mkdir(parents=True)
    (skill / "SKILL.md").write_text("Test skill")
    (skill / "references" / "cli.md").write_text("Test CLI")
    (skill / "references" / "install.md").write_text("Test installation")
    (skill / "references" / "requests.md").write_text("A new reference ships too")
    (skill / ".DS_Store").write_text("Must not publish")
    (skill / "references" / "__pycache__" / "x.pyc").write_text("Must not publish")
    destination = tmp_path / "dist"
    destination.mkdir()
    (destination / "dj_library_tool-1.2.3-py3-none-any.whl").write_bytes(b"wheel")
    (destination / "dj_library_tool-1.2.3.tar.gz").write_bytes(b"source")
    (destination / "old.whl").write_bytes(b"old build")
    assets = release_assets(tmp_path)
    archive = assets[2]
    original = archive.read_bytes()
    with ZipFile(archive) as bundle:
        assert set(bundle.namelist()) == {
            "dj-library/SKILL.md",
            "dj-library/LICENSE",
            "dj-library/references/cli.md",
            "dj-library/references/install.md",
            "dj-library/references/requests.md",
        }
    manifest = assets[3].read_text()
    for asset in assets[:3]:
        assert f"{hashlib.sha256(asset.read_bytes()).hexdigest()}  {asset.name}\n" in manifest
    assert "old.whl" not in manifest
    release_assets(tmp_path)
    assert archive.read_bytes() == original


INSTALLER = Path(__file__).resolve().parent.parent / "install.sh"


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None, reason="the installer runs in sh"
)
@pytest.mark.parametrize(
    "answer",
    ["/tmp/x/My\\ Music ", "'/tmp/x/My Music'", '"/tmp/x/My Music"', "  /tmp/x/My Music\t"],
)
def test_the_installer_takes_a_folder_dragged_into_terminal(answer):
    """Terminal types a dragged folder as /Volumes/DJ\\ Drive/Music with a trailing space."""
    assert installer_folder(answer) == "/tmp/x/My Music"


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("sh") is None, reason="the installer runs in sh"
)
def test_the_installer_expands_home_and_keeps_skip_and_quotes_in_names():
    assert installer_folder("~/Music", home="/Users/dj") == "/Users/dj/Music"
    assert installer_folder("'~/Music'", home="/Users/dj") == "/Users/dj/Music"
    assert installer_folder("~", home="/Users/dj") == "/Users/dj"
    assert installer_folder("skip") == "skip" and installer_folder("   ") == ""
    assert installer_folder("/Volumes/Rock\\'n\\'Roll/DJ") == "/Volumes/Rock'n'Roll/DJ"
    script = INSTALLER.read_text(encoding="utf-8")
    assert "Drag the folder here, Enter for ~/Music, or type skip: " in script
    # The way to do it later names the folder the DJ gave, in the form init takes now.
    assert """Later: djlib init "%s" && djlib scan""" in script


def installer_folder(answer: str, home: str = "/Users/dj") -> str:
    """The installer's own folder_answer function, run in sh on one answer."""
    lines = INSTALLER.read_text(encoding="utf-8").splitlines()
    start = lines.index("folder_answer() {")
    function = "\n".join(lines[start : lines.index("}", start) + 1])
    done = subprocess.run(
        ["sh", "-c", f'{function}\nfolder_answer "$1"', "sh", answer],
        capture_output=True,
        text=True,
        check=True,
        env={"HOME": home, "PATH": os.environ.get("PATH", "")},
    )
    return done.stdout.removesuffix("\n")
