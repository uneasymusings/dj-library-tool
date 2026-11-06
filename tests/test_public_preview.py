"""The public demo should search like the local app and keep writes disabled."""

import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("node") is None, reason="needs node")
def test_public_preview_search_and_write_boundary():
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [
            "node",
            str(root / "tests/preview_ui.cjs"),
            str(root / "src/djlib/interfaces/web/app.js"),
            str(root / "docs/preview/demo.js"),
        ],
        check=True,
        timeout=30,
    )
