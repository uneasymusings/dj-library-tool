"""Explicit source permissions survive repeated initialization and concurrent additions."""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from djlib.domain.errors import AppError
from djlib.workspace import Workspace


def test_init_again_with_a_new_folder_adds_it(tmp_path):
    original, extra, more = tmp_path / "original", tmp_path / "extra", tmp_path / "more"
    for folder in (original, extra, more):
        folder.mkdir()
    music = original / "keep.wav"
    music.write_bytes(b"preserved original")
    workspace = Workspace(tmp_path / "workspace")
    config = workspace.initialize([original])
    assert workspace.initialize([original]) == config
    # Adding a folder only lets djlib read it, so init adds it instead of refusing.
    added = workspace.initialize([extra])
    assert added.allowed_roots == [str(original), str(extra)]
    assert added.workspace_id == config.workspace_id
    with pytest.raises(AppError) as error:
        workspace.initialize([tmp_path / "missing"])
    assert error.value.code == "SOURCE_ROOT_INVALID"
    result = workspace.add_roots([more, more])
    assert result["added"] == [str(more)]
    assert workspace.add_roots([more])["added"] == []
    assert music.read_bytes() == b"preserved original"


def test_init_takes_folders_as_arguments_and_adds_them_later(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from djlib.interfaces.cli import app as cli_app

    monkeypatch.setenv("COLUMNS", "300")  # temporary paths are long
    music, crates, usb = tmp_path / "Music", tmp_path / "DJ Crates", tmp_path / "USB"
    for folder in (music, crates, usb):
        folder.mkdir()
    workspace = ["--workspace", str(tmp_path / "workspace")]

    def init(*arguments, pretty=False):
        env = {"DJLIB_OUTPUT": "pretty" if pretty else None, "NO_COLOR": "1"}
        return CliRunner(env=env).invoke(cli_app, [*workspace, "init", *map(str, arguments)])

    first = json.loads(init(music).stdout)["result"]
    assert first["config"]["allowed_roots"] == [str(music)]
    assert first["created"] is True and first["added"] == [str(music)]
    again = init(crates, pretty=True)
    assert again.exit_code == 0, again.output
    shown = " ".join(again.stdout.split())
    assert f"✓ Added {crates} to your music folders" in shown
    assert shown.endswith("Next → Index the new music djlib scan")
    # --allow-root still works, merged with folder arguments.
    both = json.loads(init(music, "--allow-root", usb).stdout)["result"]
    assert both["created"] is False and both["added"] == [str(usb)]
    assert both["config"]["allowed_roots"] == [str(music), str(crates), str(usb)]


def test_root_addition_is_all_or_nothing_and_preserves_concurrent_permissions(tmp_path):
    workspace = Workspace(tmp_path / "workspace")
    workspace.initialize()
    folders = [tmp_path / f"music-{n}" for n in range(4)]
    for folder in folders:
        folder.mkdir()
    with pytest.raises(AppError) as error:
        workspace.add_roots([folders[0], tmp_path / "missing"])
    assert error.value.code == "SOURCE_ROOT_INVALID"
    assert workspace.config().allowed_roots == []
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda folder: workspace.add_roots([folder]), folders))
    assert set(workspace.config().allowed_roots) == {str(p) for p in folders}
