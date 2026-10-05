"""Bounded version checks for yt-dlp's supported JavaScript runtimes."""

import re
import shutil
import subprocess
from functools import lru_cache

MINIMUMS = {"deno": (2, 3, 0), "node": (22, 0, 0)}


@lru_cache(maxsize=16)
def _version(path: str, program: str) -> tuple[int, int, int] | None:
    try:
        reply = subprocess.run(
            [path, "--version"], capture_output=True, text=True, timeout=3, check=True
        )
        line = reply.stdout.splitlines()[0]
        pattern = (
            r"^deno (\d+)\.(\d+)\.(\d+)(?:\s|$)"
            if program == "deno"
            else r"^v(\d+)\.(\d+)\.(\d+)(?:\s|$)"
        )
        matched = re.match(pattern, line.strip())
        return tuple(map(int, matched.groups())) if matched else None
    except (OSError, IndexError, subprocess.SubprocessError):
        return None


def javascript_runtimes() -> dict:
    """Prefer supported Deno, then supported Node; presence alone is insufficient."""
    result = {"minimum_versions_source": "https://github.com/yt-dlp/yt-dlp/wiki/EJS"}
    selected = None
    for program, minimum in MINIMUMS.items():
        path = shutil.which(program)
        version = _version(path, program) if path else None
        supported = version is not None and version >= minimum
        result[program] = {
            "path": path,
            "version": ".".join(map(str, version)) if version else None,
            "minimum_version": ".".join(map(str, minimum)),
            "supported": supported,
        }
        if supported and selected is None:
            selected = program
    result["selected"] = selected
    result["youtube_runtime_ready"] = selected is not None
    return result
