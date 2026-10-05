"""Version-gated JavaScript discovery and actionable provider errors without network calls."""

import subprocess
from types import SimpleNamespace

import pytest

from djlib.sources import runtimes, web


@pytest.fixture(autouse=True)
def isolated_runtime_cache():
    runtimes._version.cache_clear()
    yield
    runtimes._version.cache_clear()


def runtime_environment(monkeypatch, versions):
    calls = []
    monkeypatch.setattr(
        runtimes.shutil,
        "which",
        lambda program: f"/synthetic/{program}" if program in versions else None,
    )

    def run(command, **kwargs):
        calls.append((command, kwargs))
        value = versions[command[0].rsplit("/", 1)[-1]]
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(stdout=value)

    monkeypatch.setattr(runtimes.subprocess, "run", run)
    return calls


@pytest.mark.parametrize(
    ("versions", "selected"),
    [
        (
            {
                "deno": "deno 2.3.0 (stable, release, aarch64-apple-darwin)\nv8 13.5\n",
                "node": "v22.0.0\n",
            },
            "deno",
        ),
        ({"deno": "deno 2.2.9\n", "node": "v22.0.0\n"}, "node"),
        ({"node": "v22.1.0\n"}, "node"),
        ({"deno": "deno 3.0.0\n", "node": "v24.0.0\n"}, "deno"),
        ({"deno": "deno 2.2.9\n", "node": "v21.9.0\n"}, None),
        ({}, None),
    ],
)
def test_supported_runtime_selection_checks_versions(monkeypatch, versions, selected):
    calls = runtime_environment(monkeypatch, versions)
    result = runtimes.javascript_runtimes()
    assert result["selected"] == selected
    assert result["youtube_runtime_ready"] is (selected is not None)
    assert result["deno"]["minimum_version"] == "2.3.0"
    assert result["node"]["minimum_version"] == "22.0.0"
    for command, options in calls:
        assert command[1:] == ["--version"]
        assert options == {"capture_output": True, "text": True, "timeout": 3, "check": True}
    assert len(calls) == len(versions)


@pytest.mark.parametrize(
    "version",
    ["", "not a version", "deno 2.3", "deno 2.3.0-rc.1", "deno 2.3.0garbage", "v22.0.0"],
)
def test_malformed_deno_does_not_hide_supported_node(monkeypatch, version):
    runtime_environment(monkeypatch, {"deno": version, "node": "v22.0.0"})
    result = runtimes.javascript_runtimes()
    assert result["deno"]["version"] is None
    assert result["deno"]["supported"] is False
    assert result["selected"] == "node"


@pytest.mark.parametrize(
    "failure",
    [
        FileNotFoundError("disappeared binary"),
        subprocess.TimeoutExpired("deno", 3),
        subprocess.CalledProcessError(1, "deno"),
    ],
)
def test_missing_failing_or_timed_out_runtime_falls_back(monkeypatch, failure):
    runtime_environment(monkeypatch, {"deno": failure, "node": "v22.0.0"})
    result = runtimes.javascript_runtimes()
    assert result["deno"]["supported"] is False
    assert result["selected"] == "node"


@pytest.mark.parametrize("version", ["22.0.0", "v22", "v22.0.0-rc.1", "v22.0.0broken", ""])
def test_malformed_node_version_never_claims_readiness(monkeypatch, version):
    runtime_environment(monkeypatch, {"node": version})
    result = runtimes.javascript_runtimes()
    assert result["selected"] is None
    assert result["youtube_runtime_ready"] is False


def test_download_command_selects_supported_node_when_deno_is_outdated(monkeypatch):
    runtime_environment(monkeypatch, {"deno": "deno 1.46.0", "node": "v22.0.0"})
    monkeypatch.setattr(web.importlib.util, "find_spec", lambda name: object())
    command = web.command()
    assert command[command.index("--js-runtimes") + 1] == "node"
    assert "--ignore-config" in command
    assert "--no-plugin-dirs" in command
    assert "--cookies-from-browser" not in command


@pytest.mark.parametrize(
    "diagnostic",
    [
        b"ERROR: No supported JavaScript runtime could be found. https://private.example/secret",
        b"WARNING: JavaScript runtime is not supported; update it.",
    ],
)
def test_provider_error_identifies_runtime_problem_without_echoing_private_details(diagnostic):
    error = web.provider_error(diagnostic)
    assert error.code == "JAVASCRIPT_RUNTIME_REQUIRED"
    assert "Deno 2.3+ or Node 22+" in error.message
    assert "doctor" in error.message
    assert "private.example" not in error.message
    assert error.retryable is False
