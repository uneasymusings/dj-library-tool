"""Package the standalone skill and checksums for a versioned GitHub release.

Build the wheel/source archive first with `uv build`. Only the current version's
artifacts enter the checksum list; older builds in dist/ are never published.
The Claude Code/Codex plugin manifest must carry the same version as the package.
"""

import hashlib
import json
import tomllib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = Path(".claude-plugin") / "plugin.json"


def project_version(root: Path = ROOT) -> str:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]


def plugin_version(root: Path = ROOT) -> str:
    return json.loads((root / PLUGIN).read_text(encoding="utf-8"))["version"]


def check_plugin_version(root: Path = ROOT) -> None:
    """Hosts cache plugins by version, so a stale plugin.json would hide a new release."""
    if (root / PLUGIN).is_file() and plugin_version(root) != project_version(root):
        raise SystemExit(
            f"{PLUGIN} has version {plugin_version(root)} but pyproject.toml has "
            f"{project_version(root)}; update the plugin version too."
        )


def skill_files(skill: Path) -> dict[str, Path]:
    """Every file of the skill, so new references ship; hidden files and caches never do."""
    return {
        "dj-library/" + path.relative_to(skill).as_posix(): path
        for path in sorted(skill.rglob("*"))
        if path.is_file()
        and not any(
            part.startswith(".") or part == "__pycache__" for part in path.relative_to(skill).parts
        )
    }


def release_assets(root: Path = ROOT, destination: Path | None = None) -> list[Path]:
    check_plugin_version(root)
    version = project_version(root)
    destination = destination or root / "dist"
    wheel = destination / f"dj_library_tool-{version}-py3-none-any.whl"
    source = destination / f"dj_library_tool-{version}.tar.gz"
    for required in (wheel, source):
        if not required.is_file():
            raise FileNotFoundError(f"Build the current distribution first: {required.name}")
    skill = root / "skills" / "dj-library"
    archive = destination / f"dj-library-skill-{version}.zip"
    entries = {**skill_files(skill), "dj-library/LICENSE": root / "LICENSE"}
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as output:
        for name, path in sorted(entries.items()):
            info = ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            output.writestr(info, path.read_bytes())
    assets = [wheel, source, archive]
    sums = destination / "SHA256SUMS"
    sums.write_text(
        "".join(
            f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in assets
        ),
        encoding="utf-8",
    )
    return [*assets, sums]


if __name__ == "__main__":
    for asset in release_assets():
        print(asset.name)
