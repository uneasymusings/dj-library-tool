"""Run installed-wheel smoke checks using the version in pyproject.toml on every OS.

On Python 3.12 the wheel goes in as the one-line installer puts it there, with the download
and recognize extras, and song recognition must work too (scripts/check_recognition.py).
"""

import subprocess
import sys

from release_artifacts import ROOT, project_version

if __name__ == "__main__":
    wheel = ROOT / "dist" / f"dj_library_tool-{project_version()}-py3-none-any.whl"
    if not wheel.is_file():
        raise SystemExit("Build the current wheel with uv build first.")
    checks = ["check_installed.py"]
    python = f"{sys.version_info[0]}.{sys.version_info[1]}"  # this one, not whichever uv finds
    package = str(wheel)
    if sys.version_info[:2] == (3, 12):
        # install.sh: uv tool install --python 3.12 "dj-library-tool[download,recognize] @ …"
        package = f"dj-library-tool[download,recognize] @ {wheel.as_uri()}"
        checks.append("check_recognition.py")
    run = ["uv", "run", "--isolated", "--no-project", "--python", python, "--with", package]
    for check in checks:
        subprocess.run([*run, "python", str(ROOT / "scripts" / check)], cwd=ROOT, check=True)
