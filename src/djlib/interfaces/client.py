"""Thin local client; accepted work belongs to the coordinator, not this process."""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from filelock import FileLock
from filelock import Timeout as FileLockTimeout

from djlib import __version__
from djlib.domain.errors import AppError
from djlib.workspace import Workspace

HEALTH_TIMEOUT = 5
DISCOVERY_RETRY_DELAYS = (0.2, 0.5)
# Answers that a retry cannot change: nothing recorded, or a different coordinator replied.
DEFINITE_DISCOVERY_FAILURES = frozenset(
    {"no_runtime_record", "invalid_runtime_url", "identity_mismatch"}
)
# A coordinator started implicitly by a command exits after this long without use.
# `service start` (used before assistant sessions) keeps its coordinator running.
IDLE_EXIT_SECONDS = 1800


class LocalClient:
    def __init__(self, workspace: Workspace, *, allow_start: bool = True):
        self.workspace = workspace
        self.allow_start = allow_start
        self._coordinator_identity = None
        self._coordinator_version = None
        self._version_checked = False
        self._version_from_health = False
        self.last_discovery_error: str | None = None

    def _forget_coordinator(self) -> None:
        self._coordinator_identity = None
        self._coordinator_version = None
        self._version_checked = False
        self._version_from_health = False

    @staticmethod
    def _version_label(value) -> str | None:
        # Do not reflect arbitrary server diagnostic text in a client error.
        return (
            value
            if isinstance(value, str) and re.fullmatch(r"[0-9][A-Za-z0-9.!+_-]{0,99}", value)
            else None
        )

    def _observe_health(self, url: str, result: dict) -> None:
        identity = (url, result["instance_id"], result["workspace_id"])
        if identity != self._coordinator_identity:
            self._forget_coordinator()
            self._coordinator_identity = identity
        if "application_version" in result:
            self._coordinator_version = self._version_label(result["application_version"])
            self._version_checked = self._version_from_health = True
        elif self._version_from_health:
            self._coordinator_version = None
            self._version_checked = self._version_from_health = False

    def _observe_capabilities(self, value: dict) -> None:
        self._version_checked = True
        result = value.get("result") if isinstance(value, dict) and value.get("ok") else None
        if (
            isinstance(result, dict)
            and result.get("workspace_id") == self.workspace.config().workspace_id
        ):
            self._coordinator_version = self._version_label(result.get("application_version"))
        else:
            self._coordinator_version = None

    def _require_matching_version(self, url: str) -> None:
        if not self._version_checked:
            # Public a2 health responses have no application version. Capabilities
            # is authenticated and read-only, and this lookup is cached per instance.
            try:
                with httpx.Client(trust_env=False, timeout=3) as client:
                    response = client.get(f"{url}/capabilities", headers=self.headers())
                    response.raise_for_status()
                    self._observe_capabilities(response.json())
            except (httpx.HTTPError, ValueError):
                self._coordinator_version = None
                self._version_checked = True
        if self._coordinator_version != __version__:
            observed = self._coordinator_version or "an unknown application version"
            raise AppError(
                "COORDINATOR_VERSION_MISMATCH",
                f"The running coordinator reports {observed}; this client requires {__version__}. "
                "Run 'djlib --workspace WORKSPACE service stop' for this workspace, then retry. "
                "Accepted jobs remain stored. No requested operation was sent or resubmitted.",
                409,
            )

    def discover(self) -> str | None:
        """The live coordinator's URL, or None. Transient failures are retried briefly.

        On Windows, a file-sharing conflict or a dropped loopback connection can make one
        probe fail while the coordinator is healthy; concluding "absent" would start a
        second one or block an MCP session. A missing runtime record fails immediately.
        """
        for delay in DISCOVERY_RETRY_DELAYS:
            if url := self._discover_once():
                self.last_discovery_error = None
                return url
            if self.last_discovery_error in DEFINITE_DISCOVERY_FAILURES:
                return None
            time.sleep(delay)
        return self._discover_once()

    def _discover_once(self) -> str | None:
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
                self.last_discovery_error = "invalid_runtime_url"
                self._forget_coordinator()
                return None
            # A stopped coordinator refuses the connection immediately. A live one can
            # answer slowly while its worker decodes audio on a loaded machine, so a short
            # read timeout would misreport it as absent (seen on Windows CI runners).
            with httpx.Client(trust_env=False, timeout=HEALTH_TIMEOUT) as client:
                reply = client.get(f"{url}/health", headers=self.headers()).json()
            result = reply.get("result", {}) if isinstance(reply, dict) else {}
            if (
                isinstance(reply, dict)
                and reply.get("ok")
                and isinstance(result, dict)
                and result.get("instance_id") == record["instance_id"]
                and (
                    result.get("workspace_id") == self.workspace.config().workspace_id
                    and result.get("protocol_version") == "1"
                )
            ):
                self._observe_health(url, result)
                return url
        except FileNotFoundError:
            self.last_discovery_error = "no_runtime_record"
            self._forget_coordinator()
            return None
        except (OSError, ValueError, KeyError, httpx.HTTPError) as exc:
            self.last_discovery_error = type(exc).__name__
            self._forget_coordinator()
            return None
        self.last_discovery_error = "identity_mismatch"
        self._forget_coordinator()
        return None

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.workspace.token()}"}

    def start(self) -> str:
        """Start or reuse a compatible coordinator before launching an assistant host."""
        url = self.ensure(idle_exit=None)
        self._require_matching_version(url)
        return url

    def ensure(self, idle_exit: float | None = IDLE_EXIT_SECONDS) -> str:
        self.workspace.config()
        if not self.allow_start:
            if url := self.discover():
                return url
            # A Windows MCP host can own a kill-on-close Job Object. Console
            # detachment does not let a spawned coordinator outlive that host.
            raise AppError(
                "COORDINATOR_START_REQUIRED",
                "No running coordinator is available. Run 'djlib --workspace WORKSPACE "
                "service start' from a terminal outside MCP, or start the assistant session "
                "with its generated launch.py. This connection cannot start a persistent "
                "coordinator. Accepted jobs remain stored; no operation was submitted.",
                503,
                False,
            )
        try:
            with FileLock(self.workspace.runtime / "startup.lock", timeout=45):
                return self._start_coordinator(idle_exit)
        except FileLockTimeout:
            raise AppError(
                "SERVICE_START_BUSY",
                "Another client is starting the coordinator. Check service status and retry; "
                "no operation was submitted.",
                503,
                True,
            ) from None

    def _start_coordinator(self, idle_exit: float | None = None) -> str:
        """Spawn once under the startup lock and wait for authenticated readiness."""
        if url := self.discover():
            return url
        kwargs = (
            {"start_new_session": True}
            if os.name != "nt"
            else {
                "creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            }
        )
        log_path = self.workspace.runtime / "service.log"
        with log_path.open("ab") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "djlib.interfaces.service",
                    "--workspace",
                    str(self.workspace.root),
                    *(["--idle-exit", str(idle_exit)] if idle_exit else []),
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                close_fds=True,
                **kwargs,
            )
        deadline = time.monotonic() + 30
        while True:
            if url := self.discover():
                return url
            exit_code = process.poll()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            # An exited child may have lost the coordinator lock to an existing
            # primary whose health endpoint is not ready yet. Wait, never respawn.
            time.sleep(min(0.1, remaining))
        if exit_code is not None:
            raise AppError(
                "SERVICE_START_FAILED",
                f"Coordinator startup process exited with code {exit_code}; no healthy service "
                f"was discovered within 30 seconds. See {log_path}.",
                503,
                True,
            )
        raise AppError(
            "SERVICE_START_TIMEOUT",
            "Coordinator did not become healthy within 30 seconds; its startup process is "
            f"still running. Check service status before retrying. See {log_path}.",
            503,
            True,
        )

    def request(
        self, method: str, path: str, *, data: dict | None = None, params: dict | None = None
    ) -> dict:
        url = self.ensure()
        administrative = (method.upper(), path) in {
            ("GET", "/health"),
            ("GET", "/capabilities"),
            ("POST", "/shutdown"),
        }
        if not administrative:
            self._require_matching_version(url)
        timeout = 15
        if path == "/sources/inspect":
            timeout = 100
        elif path.startswith("/deliveries/"):
            # Readback has a 120-second scan budget plus volume probes. Tag-only
            # reconciliation can decode changed working copies; do not cut it off at 15s.
            timeout = 600 if path.endswith(("/observations", "/verify-app")) else 180
        elif path.startswith(("/requests", "/organization", "/annotations", "/recordings/")):
            timeout = 180
        try:
            with httpx.Client(trust_env=False, timeout=timeout) as client:
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
                if (
                    method.upper() == "GET"
                    and path == "/capabilities"
                    and not self._version_from_health
                ):
                    self._observe_capabilities(value)
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
