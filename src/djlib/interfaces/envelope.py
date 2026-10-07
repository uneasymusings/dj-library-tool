"""The JSON response envelope shared by the CLI, MCP server and HTTP service.

Kept free of server imports so the CLI starts quickly.
"""

from uuid import uuid4


def envelope(result: dict | None = None, error: dict | None = None) -> dict:
    return {
        "schema_version": "1",
        "ok": error is None,
        "request_id": f"req_{uuid4().hex}",
        "result": result,
        "warnings": [],
        "error": error,
    }
