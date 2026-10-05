"""Explicit source permissions survive repeated initialization and concurrent additions."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from djlib.domain.errors import AppError
from djlib.workspace import Workspace


def test_existing_init_never_silently_ignores_new_root(tmp_path):
    original, extra = tmp_path / "original", tmp_path / "extra"
    original.mkdir()
    extra.mkdir()
    music = original / "keep.wav"
    music.write_bytes(b"preserved original")
    workspace = Workspace(tmp_path / "workspace")
    config = workspace.initialize([original])
    assert workspace.initialize([original]) == config
    with pytest.raises(AppError) as error:
        workspace.initialize([extra])
    assert error.value.code == "ROOTS_NOT_UPDATED"
    assert workspace.config() == config
    result = workspace.add_roots([extra, extra])
    assert result["added"] == [str(extra)]
    assert workspace.add_roots([extra])["added"] == []
    assert workspace.config().workspace_id == config.workspace_id
    assert music.read_bytes() == b"preserved original"


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
