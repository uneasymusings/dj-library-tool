"""Workspace ownership, safe paths, and private local runtime files."""

import json
import os
import secrets
import tempfile
from pathlib import Path
from uuid import uuid4

from filelock import FileLock
from pydantic import Field

from djlib.domain.contracts import Contract, Profile
from djlib.domain.errors import AppError


class WorkspaceConfig(Contract):
    schema_version: str = "1"
    workspace_id: str
    allowed_roots: list[str] = Field(default_factory=list)
    profiles: dict[str, Profile] = Field(
        default_factory=lambda: {
            "club": Profile(),
            "archive": Profile(name="archive", copy_into_library=True),
        }
    )


def missing_folder(path: Path) -> str:
    return f"{path} isn't an existing folder. Check the path, or plug the drive back in."


def atomic_json(path: Path, data: dict) -> None:
    """Write complete private JSON, without exposing a half-written runtime record."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class Workspace:
    """A catalog/storage boundary; preference profiles never own separate daemons."""

    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.config_path = self.root / "workspace.json"
        self.runtime = self.root / "runtime"
        self.database = self.root / "catalog.db"
        self.managed = self.root / "media"
        self.staging = self.root / "staging"
        self.exports = self.root / "exports"
        self.incoming = self.root / "incoming"

    def initialize(self, allowed_roots: list[Path] | None = None) -> WorkspaceConfig:
        if self.config_path.exists():
            config = self.config()
            if allowed_roots and any(
                str(p.expanduser().resolve()) not in config.allowed_roots for p in allowed_roots
            ):
                raise AppError(
                    "ROOTS_NOT_UPDATED",
                    "This workspace exists. Use 'roots add PATH' to explicitly add folders.",
                    409,
                )
            return config
        if self.root.exists() and any(self.root.iterdir()):
            raise AppError("WORKSPACE_NOT_EMPTY", "Choose an empty directory for a new workspace.")
        roots = []
        for value in allowed_roots or []:
            try:
                root = value.expanduser().resolve(strict=True)
                if not root.is_dir():
                    raise ValueError("Allowed roots must be directories.")
            except (OSError, ValueError) as exc:
                raise AppError("SOURCE_ROOT_INVALID", missing_folder(value)) from exc
            roots.append(str(root))
        self.root.mkdir(parents=True, mode=0o700, exist_ok=True)
        if os.name != "nt":
            self.root.chmod(0o700)
        for directory in (self.runtime, self.managed, self.staging, self.exports, self.incoming):
            directory.mkdir(mode=0o700)
        config = WorkspaceConfig(
            workspace_id=f"ws_{uuid4().hex}",
            allowed_roots=roots,
        )
        atomic_json(self.config_path, config.model_dump(mode="json"))
        token_path = self.runtime / "token"
        token_path.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        token_path.chmod(0o600)
        return config

    def add_roots(self, values: list[Path]) -> dict:
        """Expand read permission explicitly; never move music or remove existing roots."""
        if not values:
            raise AppError("SOURCE_ROOT_INVALID", "Supply at least one existing music folder.")
        roots = []
        for value in values:
            if any(ord(c) < 32 or ord(c) == 127 for c in str(value)):
                raise AppError(
                    "SOURCE_ROOT_INVALID", "Folder names can't contain control characters."
                )
            try:
                root = value.expanduser().resolve(strict=True)
                if not root.is_dir():
                    raise ValueError("Not a directory")
            except (OSError, ValueError) as exc:
                raise AppError("SOURCE_ROOT_INVALID", missing_folder(value)) from exc
            roots.append(str(root))
        with FileLock(self.root / "configuration.lock", timeout=10):
            config = self.config()
            additions = [root for root in dict.fromkeys(roots) if root not in config.allowed_roots]
            if additions:
                config.allowed_roots += additions
                atomic_json(self.config_path, config.model_dump(mode="json"))
        return {"allowed_roots": config.allowed_roots, "added": additions, "music_modified": False}

    def config(self) -> WorkspaceConfig:
        if not self.config_path.is_file():
            raise AppError(
                "WORKSPACE_REQUIRED", f"djlib isn't set up yet (no workspace in {self.root})."
            )
        try:
            return WorkspaceConfig.model_validate_json(self.config_path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise AppError("CONFIG_INVALID", "The workspace configuration is invalid.") from exc

    def token(self) -> str:
        try:
            return (self.runtime / "token").read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise AppError(
                "TOKEN_MISSING", "The local service token is missing; run doctor."
            ) from exc

    def authorize(self, value: str, *, directory: bool = False) -> Path:
        """Resolve symlinks before checking boundaries, including when reusing assets."""
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise AppError("PATH_INVALID", "Media paths cannot contain control characters.")
        try:
            path = Path(value).expanduser().resolve(strict=True)
        except OSError as exc:
            raise AppError(
                "FILE_UNAVAILABLE",
                f"Can't find {value}. Check the path, or plug the drive back in.",
            ) from exc
        roots = [self.root, *(Path(p) for p in self.config().allowed_roots)]
        if not any(path.is_relative_to(root) for root in roots):
            raise AppError("SOURCE_NOT_ALLOWED", f"djlib isn't allowed to read {path} yet.", 403)
        if directory and not path.is_dir():
            raise AppError("DIRECTORY_REQUIRED", "The scan requires a directory.")
        if not directory and not path.is_file():
            raise AppError("FILE_REQUIRED", "The request requires a regular file.")
        return path
