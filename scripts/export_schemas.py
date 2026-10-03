"""Regenerate the stable checked-in public input schemas from CLI schema output."""

import json
import subprocess
import sys
from pathlib import Path

reply = subprocess.run(
    [sys.executable, "-m", "djlib.interfaces.cli", "schemas"],
    capture_output=True,
    text=True,
    check=True,
)
contracts = json.loads(reply.stdout)["result"]
path = Path(__file__).resolve().parent.parent / "schemas" / "inputs.json"
path.parent.mkdir(exist_ok=True)
path.write_text(
    json.dumps(
        {"schema_version": "1", "contracts": contracts},
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    )
    + "\n",
    encoding="utf-8",
)
