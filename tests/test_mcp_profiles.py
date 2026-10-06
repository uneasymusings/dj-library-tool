"""What an assistant sees first: the default tool profile, compact replies, no-workspace answers
and capabilities that point rekordbox and USB work at the CLI."""

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from mcp import Client

from djlib.interfaces import client as client_module
from djlib.interfaces import mcp_server
from djlib.interfaces.client import LocalClient
from djlib.interfaces.envelope import envelope
from djlib.interfaces.tool_manifest import CORE_PROFILE, TOOL_NAMES, profile_tools
from djlib.workspace import Workspace


async def tool_names(workspace) -> set[str]:
    async with Client(mcp_server.build_server(workspace)) as client:
        return {tool.name for tool in (await client.list_tools()).tools}


def test_core_profile_is_a_subset_of_the_full_inventory():
    assert CORE_PROFILE < TOOL_NAMES
    assert len(CORE_PROFILE) == 19
    assert profile_tools(None) == profile_tools("") == profile_tools("core") == CORE_PROFILE
    assert profile_tools("full") == profile_tools(" FULL ") == TOOL_NAMES
    assert profile_tools("everything") == CORE_PROFILE


async def test_default_server_exposes_the_core_profile(application):
    assert await tool_names(application.workspace) == CORE_PROFILE


@pytest.mark.parametrize(("value", "expected"), [("full", TOOL_NAMES), ("core", CORE_PROFILE)])
async def test_environment_selects_the_profile(application, monkeypatch, value, expected):
    monkeypatch.setenv("DJLIB_MCP_TOOLS", value)
    assert await tool_names(application.workspace) == expected


async def test_instructions_send_rekordbox_and_usb_to_the_cli(application):
    async with Client(mcp_server.build_server(application.workspace)) as client:
        instructions = client.instructions
    assert "dj-library skill" in instructions
    assert "djlib rekordbox push|usb" in instructions and "background" in instructions
    assert "untrusted" in instructions


async def test_text_content_is_compact_and_structured_content_is_unchanged(
    application, monkeypatch
):
    reply = envelope({"tracks": [{"artist": "Ä", "title": "Track"}], "total": 1})
    monkeypatch.setattr(LocalClient, "request", lambda self, *a, **kw: reply)
    async with Client(mcp_server.build_server(application.workspace)) as client:
        result = await client.call_tool("djlib_library", {"query": "x"})
    assert not result.is_error
    assert result.structured_content == reply
    (text,) = result.content
    assert "\n" not in text.text and ": " not in text.text and ", " not in text.text
    assert json.loads(text.text) == reply


async def test_validation_errors_are_compact_too(application):
    async with Client(mcp_server.build_server(application.workspace)) as client:
        result = await client.call_tool("djlib_create_request", {"request_body": {"items": 1}})
    assert result.is_error
    assert result.structured_content["error"]["code"] == "INPUT_INVALID"
    assert "\n" not in result.content[0].text
    assert json.loads(result.content[0].text) == result.structured_content


async def test_server_without_a_workspace_answers_every_call(tmp_path, monkeypatch):
    monkeypatch.setenv("DJLIB_MCP_TOOLS", "full")
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    workspace = Workspace(tmp_path / "home" / ".local/share/djlib/default")

    def forbidden(*args, **kwargs):
        pytest.fail("No coordinator may start without a workspace")

    monkeypatch.setattr(client_module.subprocess, "Popen", forbidden)
    calls = {
        "djlib_capabilities": {},
        "djlib_library": {"query": "x"},
        "djlib_roots": {},
        "djlib_scan": {"path": "/music", "idempotency_key": "scan"},
        "djlib_job": {"job_id": "job_x"},
        "djlib_find_sources": {"artist": "A", "title": "T"},
        "djlib_create_request": {
            "request_body": {"name": "Set", "idempotency_key": "k", "items": [{"artist": "A"}]}
        },
        "djlib_import_rekordbox_analysis": {},
        "djlib_delivery_targets": {},
    }
    async with Client(mcp_server.build_server(workspace)) as client:
        assert {tool.name for tool in (await client.list_tools()).tools} == TOOL_NAMES
        for name, arguments in calls.items():
            reply = await client.call_tool(name, arguments)
            error = reply.structured_content["error"]
            if name == "djlib_create_request":
                # Malformed input is still reported first, without touching the workspace.
                assert error["code"] == "INPUT_INVALID"
                continue
            assert error["code"] == "WORKSPACE_REQUIRED", (name, error)
            assert "djlib init --allow-root ~/Music" in error["message"]
            assert "--workspace" not in error["message"]
        # Once `djlib init` has run, the same server reaches the coordinator.
        workspace.initialize()
        monkeypatch.setattr(LocalClient, "request", lambda self, *a, **kw: envelope({"x": 1}))
        reply = await client.call_tool("djlib_capabilities", {})
        assert reply.structured_content["ok"] is True


async def test_missing_custom_workspace_names_it(tmp_path):
    workspace = Workspace(tmp_path / "elsewhere")
    async with Client(mcp_server.build_server(workspace)) as client:
        reply = await client.call_tool("djlib_roots", {})
    message = reply.structured_content["error"]["message"]
    assert "djlib init --allow-root ~/Music" in message
    assert f"--workspace {workspace.root}" in message


def test_stdio_server_starts_without_a_workspace(tmp_path):
    """The server process itself never needs a workspace to start (cli.py aside)."""
    script = (
        "import sys; from pathlib import Path; "
        "from djlib.interfaces.mcp_server import build_server; "
        "from djlib.workspace import Workspace; "
        "build_server(Workspace(Path(sys.argv[1]))).run(transport='stdio')"
    )
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "djlib_capabilities", "arguments": {}},
        },
    ]
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(tmp_path / "missing")],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    replies = {}
    watchdog = threading.Timer(60, process.kill)  # a silent server fails instead of hanging
    watchdog.start()
    try:
        for message in messages:
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()
            if "id" in message:
                # stdout carries protocol messages only: every line is JSON-RPC.
                line = json.loads(process.stdout.readline())
                replies[line["id"]] = line
    finally:
        process.stdin.close()
        returncode = process.wait(timeout=30)
        watchdog.cancel()
    assert returncode == 0, process.stderr.read()
    assert {tool["name"] for tool in replies[2]["result"]["tools"]} == CORE_PROFILE
    result = replies[3]["result"]
    assert result["structuredContent"]["error"]["code"] == "WORKSPACE_REQUIRED"
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    assert not (tmp_path / "missing").exists()


def test_capabilities_point_rekordbox_and_usb_at_the_cli(application, monkeypatch):
    from djlib.native import rekordbox_mac

    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(rekordbox_mac, "installed", lambda: {"path": "/x", "version": "7.2.19"})
    capabilities = application.capabilities()
    assert capabilities["native_automation_available"] is True
    assert capabilities["cli_only"] == [
        "djlib set FILE_OR_URL [--fetch --yes] [--usb] [--no-rekordbox]",
        "djlib rekordbox push ID",
        "djlib rekordbox usb ID",
    ]
    implemented = set(capabilities["implemented"])
    assert {
        "rekordbox_push",
        "rekordbox_usb_export",
        "rekordbox_analysis_sync",
        "rekordbox_analysis_pull",
        "usb_device_library_verification",
        "web_source_search",
        "selected_web_audio_download",
        "set_link_tracklists",
        "listener_comment_id_hints",
    } <= implemented
    assert not implemented & set(capabilities["planned"])
    assert not {"native_rekordbox", "device_export", "online_source_discovery"} & set(
        capabilities["planned"]
    )
    assert "MP3" in capabilities["details"]["web_sources"]

    monkeypatch.setattr(rekordbox_mac, "installed", lambda: None)
    assert application.capabilities()["native_automation_available"] is False
    monkeypatch.setattr(rekordbox_mac, "installed", lambda: {"path": "/x", "version": "7"})
    monkeypatch.setattr(sys, "platform", "linux")
    assert application.capabilities()["native_automation_available"] is False
