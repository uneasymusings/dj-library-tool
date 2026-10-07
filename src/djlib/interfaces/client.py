"""Thin local client; accepted work belongs to the coordinator, not this process."""

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from filelock import FileLock
from filelock import Timeout as FileLockTimeout

import djlib
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
# How a client older than the running djlib gets the newer one; the MCP server says reconnect.
UPDATE_FIX = "Run the newer djlib (`djlib upgrade` installs it), then try again."
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?(?:\.post(\d+))?(?:\.dev(\d+))?")


def version_key(value: str) -> tuple:
    """Order versions like 0.1.0a10 > 0.1.0a9 > 0.1.0a9.dev0 without extra dependencies."""
    match = VERSION.match(value)
    if not match:
        return (0,)
    major, minor, patch, stage, number, post, dev = match.groups()
    rank = {"a": 0, "b": 1, "rc": 2, None: 3}[stage]
    if stage is None and post is None and dev is not None:
        rank = -1  # 0.2.0.dev1 comes before 0.2.0a1
    post_number = -1 if post is None else int(post)
    numbers = (int(major), int(minor), int(patch), rank, int(number or 0), post_number)
    return (*numbers, dev is None, int(dev or 0))


def installed_version() -> str | None:
    """The djlib version on disk now, which a newly started process would run.

    Read from this package's own ``__init__.py``, so it is right for editable installs
    too; None when it can't be read.
    """
    try:
        text = Path(djlib.__file__).read_text(encoding="utf-8")
    except (OSError, TypeError, ValueError):
        return None
    match = re.search(r"""^__version__ = ["']([^"'\s]+)["']""", text, re.MULTILINE)
    return match[1] if match else None


def client_outdated(newer: str, fix: str, *, installed: bool = False) -> AppError:
    """This process runs older code than the background service or the installed djlib."""
    if installed:
        problem = f"djlib {newer} is now installed, but this session still runs {__version__}"
    else:
        problem = (
            f"This djlib session ({__version__}) is older than the background djlib "
            f"({newer}) that's running"
        )
    return AppError(
        "CLIENT_OUTDATED",
        f"{problem}, so nothing was sent. {fix.format(version=newer)}",
        409,
        False,
    )


class LocalClient:
    def __init__(
        self, workspace: Workspace, *, allow_start: bool = True, outdated_fix: str = UPDATE_FIX
    ):
        self.workspace = workspace
        self.allow_start = allow_start
        self.outdated_fix = outdated_fix
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
        observed = self._coordinator_version
        if observed == __version__:
            return
        if observed and version_key(observed) > version_key(__version__):
            # A newer service is never stopped for an older client; this client must go.
            raise client_outdated(observed, self.outdated_fix)
        stop = "djlib service stop"
        if self.workspace.root != default_workspace():
            stop = f"djlib --workspace {shlex.quote(str(self.workspace.root))} service stop"
        which = "A djlib with an unknown application version"
        if observed:
            which = f"An older djlib ({observed})"
        raise AppError(
            "COORDINATOR_VERSION_MISMATCH",
            f"{which} is still running in the background and is busy or owned by another "
            f"app, so it wasn't restarted. This is {__version__}. When it's done, run "
            f"`{stop}` and try again; saved jobs are kept and nothing was sent.",
            409,
        )

    def _restart_outdated(self, url: str) -> bool:
        """Stop an older, idle background service so this version starts its own.

        Returns False (and leaves it running) when this client may not start a service, or
        when the old one is running jobs or can't be asked; the caller then explains.
        """
        if not self.allow_start:
            return False
        installed = installed_version()
        if installed and installed != __version__:
            # A replacement would run the code on disk, not this version: it would never match.
            raise client_outdated(installed, self.outdated_fix, installed=True)
        try:
            with httpx.Client(trust_env=False, timeout=5) as client:
                reply = client.get(f"{url}/jobs", params={"limit": 50}, headers=self.headers())
                reply.raise_for_status()
                jobs = (reply.json().get("result") or {}).get("jobs") or []
                if any(job.get("state") in {"queued", "running"} for job in jobs):
                    return False
                client.post(f"{url}/shutdown", headers=self.headers()).raise_for_status()
        except (httpx.HTTPError, ValueError, AttributeError):
            return False
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            self._forget_coordinator()
            current = self._discover_once()
            if current is None:
                return True  # gone: the caller starts this version's service
            try:
                self._require_matching_version(current)
                return True  # already replaced by a matching service
            except AppError as error:
                if error.code == "CLIENT_OUTDATED":
                    raise  # a newer djlib took over; waiting won't help
                time.sleep(0.2)
        return False

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
            try:
                self._require_matching_version(url)
            except AppError as error:
                # After an update the old background service is usually idle: replace it.
                if error.code != "COORDINATOR_VERSION_MISMATCH" or not self._restart_outdated(url):
                    raise
                url = self.ensure()
                self._require_matching_version(url)
        timeout = 15
        if path == "/sources/inspect":
            # Fetching listener comments takes up to the adapter's 240-second budget.
            timeout = 260 if (data or {}).get("comments") else 100
        elif path == "/sources/search":
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


def config_dir() -> Path:
    """Per-user djlib settings (not music or catalog data)."""
    if override := os.environ.get("DJLIB_CONFIG_DIR"):
        return Path(override)
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "djlib"
    return Path.home() / ".config" / "djlib"


def remembered_workspace() -> Path | None:
    try:
        value = (config_dir() / "workspace").read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return Path(value) if value else None


def remember_workspace(path: Path) -> None:
    folder = config_dir()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "workspace").write_text(str(path) + "\n", encoding="utf-8")


def default_workspace() -> Path:
    """DJLIB_WORKSPACE, else the workspace chosen with `djlib use`, else the built-in default."""
    if value := os.environ.get("DJLIB_WORKSPACE"):
        return Path(value)
    return remembered_workspace() or Path.home() / ".local/share/djlib/default"
