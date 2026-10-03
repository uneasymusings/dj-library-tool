"""Run installed-wheel smoke checks using the version in pyproject.toml on every OS."""

import subprocess

from release_artifacts import ROOT, project_version

if __name__ == "__main__":
    wheel = ROOT / "dist" / f"dj_library_tool-{project_version()}-py3-none-any.whl"
    if not wheel.is_file():
        raise SystemExit("Build the current wheel with uv build first.")
    subprocess.run(
        [
            "uv",
            "run",
            "--isolated",
            "--no-project",
            "--with",
            str(wheel),
            "python",
            str(ROOT / "scripts" / "check_installed.py"),
        ],
        cwd=ROOT,
        check=True,
    )
