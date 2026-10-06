"""Boundary tests for host input, paths, and initialization recovery."""

import json
import os

import pytest
from pydantic import ValidationError

from djlib.domain.contracts import CollectionRequest, TrackInput, normalize, recording_key
from djlib.domain.errors import AppError
from djlib.workspace import Workspace, atomic_json


@pytest.mark.parametrize(
    "field,value",
    [
        ("artist", " "),
        ("title", "\t"),
        ("artist", "x\ny"),
        ("version", "dub\x00mix"),
        ("title", "x\x7fy"),
    ],
)
def test_invalid_labels(field, value):
    values = {"path": "/music/a.wav", "artist": "Artist", "title": "Track", field: value}
    with pytest.raises(ValidationError):
        TrackInput(**values)


def test_strict_unknown_fields():
    with pytest.raises(ValidationError):
        CollectionRequest(name="Set", tracks=[], typo=True)


def test_unicode_normalization_preserves_versions():
    assert normalize(" Ａrtist — TRACK ") == "artist track"
    assert recording_key("A", "Track", "Extended") != recording_key("A", "Track", "Radio")


def test_invalid_root_does_not_leave_half_workspace(tmp_path):
    workspace = Workspace(tmp_path / "library")
    with pytest.raises(AppError) as error:
        workspace.initialize([tmp_path / "absent"])
    # The message names the folder that is wrong.
    assert error.value.code == "SOURCE_ROOT_INVALID"
    assert f"{tmp_path / 'absent'} isn't an existing folder" in error.value.message
    assert not workspace.root.exists()
    with pytest.raises(AppError) as error:
        workspace.config()
    assert error.value.code == "WORKSPACE_REQUIRED"
    assert error.value.message.startswith("djlib isn't set up yet")
    workspace.initialize()
    assert workspace.token()
    (tmp_path / "music").mkdir()
    with pytest.raises(AppError) as error:
        workspace.add_roots([tmp_path / "music", tmp_path / "gone"])
    assert error.value.code == "SOURCE_ROOT_INVALID"
    assert error.value.message.startswith(f"{tmp_path / 'gone'} isn't an existing folder")
    assert workspace.config().allowed_roots == []  # all or nothing


def test_nonempty_workspace_refused(tmp_path):
    (tmp_path / "important.txt").write_text("preserve")
    with pytest.raises(AppError) as error:
        Workspace(tmp_path).initialize()
    assert error.value.code == "WORKSPACE_NOT_EMPTY"
    assert (tmp_path / "important.txt").read_text() == "preserve"


def test_init_repeated_preserves_identity_and_token(tmp_path):
    workspace = Workspace(tmp_path / "library")
    first = workspace.initialize()
    token = workspace.token()
    assert workspace.initialize().workspace_id == first.workspace_id
    assert workspace.token() == token
    if os.name != "nt":
        assert workspace.root.stat().st_mode & 0o777 == 0o700
        assert (workspace.runtime / "token").stat().st_mode & 0o777 == 0o600


def test_outside_path_and_symlink_denied(application, tmp_path):
    outside = tmp_path / "outside.wav"
    outside.write_bytes(b"outside")
    with pytest.raises(AppError) as error:
        application.workspace.authorize(str(outside))
    assert error.value.code == "SOURCE_NOT_ALLOWED"
    assert error.value.message == f"djlib isn't allowed to read {outside.resolve()} yet."
    link = tmp_path / "music" / "linked.wav"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation unavailable")
    with pytest.raises(AppError) as error:
        application.workspace.authorize(str(link))
    assert error.value.code == "SOURCE_NOT_ALLOWED"


def test_unavailable_and_wrong_path_type(application, tmp_path):
    with pytest.raises(AppError) as error:
        application.workspace.authorize(str(tmp_path / "missing"))
    assert error.value.code == "FILE_UNAVAILABLE"
    assert error.value.message == (
        f"Can't find {tmp_path / 'missing'}. Check the path, or plug the drive back in."
    )
    with pytest.raises(AppError) as error:
        application.workspace.authorize(str(application.workspace.root))
    assert error.value.code == "FILE_REQUIRED"
    with pytest.raises(AppError) as error:
        application.workspace.authorize(str(tmp_path / "music" / "tone.wav"), directory=True)
    assert error.value.code == "DIRECTORY_REQUIRED"


def test_control_characters_in_paths_denied(application):
    with pytest.raises(AppError) as error:
        application.workspace.authorize("bad\npath.wav")
    assert error.value.code == "PATH_INVALID"


def test_atomic_json_replaces_complete_document(tmp_path):
    path = tmp_path / "nested" / "data.json"
    atomic_json(path, {"unicode": "音楽"})
    atomic_json(path, {"second": 2})
    assert json.loads(path.read_text()) == {"second": 2}
    assert list(path.parent.iterdir()) == [path]
