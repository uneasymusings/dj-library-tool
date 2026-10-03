"""Thin local client; accepted work belongs to the coordinator, not this process."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from filelock import FileLock

from djlib.domain.errors import AppError
from djlib.workspace import Workspace


class LocalClient:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    def discover(self) -> str | None:
        try:
            record = json.loads(
                (self.workspace.runtime / "service.json").read_text(encoding="utf-8")
            )
            url = record["url"]
            # Runtime records never grant permission to connect to arbitrary hosts.
            parts = urlsplit(url)
            if (
                parts.scheme != "http"
                or parts.hostname != "127.0.0.1"
                or not parts.port
                or parts.username
                or parts.password
                or parts.path
                or parts.query
                or parts.fragment
            ):
                return None
            with httpx.Client(trust_env=False, timeout=1) as client:
                reply = client.get(f"{url}/health", headers=self.headers()).json()
            result = reply.get("result", {})
            if (
                reply.get("ok")
                and result.get("instance_id") == record["instance_id"]
                and (
                    result.get("workspace_id") == self.workspace.config().workspace_id
                    and result.get("protocol_version") == "1"
                )
            ):
                return url
        except (OSError, ValueError, KeyError, httpx.HTTPError):
            return None
        return None

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.workspace.token()}"}

    def ensure(self) -> str:
        self.workspace.config()
        with FileLock(self.workspace.runtime / "startup.lock", timeout=15):
            if url := self.discover():
                return url
            kwargs = (
                {"start_new_session": True}
                if os.name != "nt"
                else {
                    "creationflags": subprocess.DETACHED_PROCESS
                    | subprocess.CREATE_NEW_PROCESS_GROUP
                }
            )
            log_path = self.workspace.runtime / "service.log"
            with log_path.open("ab") as log:
                subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "djlib.interfaces.service",
                        "--workspace",
                        str(self.workspace.root),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    close_fds=True,
                    **kwargs,
                )
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if url := self.discover():
                    return url
                time.sleep(0.1)
            raise AppError(
                "SERVICE_UNAVAILABLE",
                f"Local coordinator did not start. See {log_path}.",
                503,
                True,
            )

    def request(
        self, method: str, path: str, *, data: dict | None = None, params: dict | None = None
    ) -> dict:
        url = self.ensure()
        try:
            with httpx.Client(
                trust_env=False, timeout=100 if path == "/sources/inspect" else 15
            ) as client:
                response = client.request(
                    method, url + path, json=data, params=params, headers=self.headers()
                )
                value = response.json()
                if not value.get("ok"):
                    error = value.get("error") or {}
                    raise AppError(
                        error.get("code", "REQUEST_FAILED"),
                        error.get("message", "Local request failed."),
                        response.status_code,
                        error.get("retryable", False),
                    )
                return value
        except (httpx.HTTPError, ValueError) as exc:
            # Do not blindly resubmit a mutation after an ambiguous transport failure.
            raise AppError(
                "TRANSPORT_UNCERTAIN",
                "Connection lost; retry with the same idempotency key.",
                503,
                True,
            ) from exc

    def wait(self, job_id: str, timeout: float = 30) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            reply = self.request("GET", f"/jobs/{job_id}")
            state = reply["result"]["state"]
            if state not in {"queued", "running"}:
                return reply
            if time.monotonic() >= deadline:
                reply["result"]["timed_out"] = True
                return reply
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))


def default_workspace() -> Path:
    return Path(os.environ.get("DJLIB_WORKSPACE", Path.home() / ".local/share/djlib/default"))
