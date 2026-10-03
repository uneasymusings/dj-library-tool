"""Release identity, standalone skill contents, and integrity manifests."""

import hashlib
import tomllib
from pathlib import Path
from zipfile import ZipFile

from djlib import __version__
from scripts.release_artifacts import project_version, release_assets


def test_release_version_matches_engine():
    root = Path(__file__).resolve().parent.parent
    assert project_version(root) == __version__
    packages = tomllib.loads((root / "uv.lock").read_text())["package"]
    locked_project = next(package for package in packages if package["name"] == "dj-library-tool")
    assert locked_project["version"] == __version__


def test_skill_archive_is_complete_reproducible_and_allowlisted(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    (tmp_path / "LICENSE").write_text("Test license")
    skill = tmp_path / "skills" / "dj-library"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text("Test skill")
    (skill / "references" / "cli.md").write_text("Test CLI")
    (skill / "references" / "install.md").write_text("Test installation")
    (skill / "private.log").write_text("Must not publish")
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
        }
    manifest = assets[3].read_text()
    for asset in assets[:3]:
        assert f"{hashlib.sha256(asset.read_bytes()).hexdigest()}  {asset.name}\n" in manifest
    assert "old.whl" not in manifest
    release_assets(tmp_path)
    assert archive.read_bytes() == original
