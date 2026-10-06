# Install the engine and connect an assistant

The skill needs the local `djlib` engine: Python runs on the user's computer to read their music, drive rekordbox and check USB sticks. No checkout or PyPI account is needed.

## Engine

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed (macOS: also `brew install ffmpeg`):

```bash
uv tool install --python 3.13 'dj-library-tool[download] @ https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a11/dj_library_tool-0.1.0a11-py3-none-any.whl'
djlib version
djlib init --allow-root ~/Music
djlib scan
djlib doctor
```

Use the newest release from the [releases page](https://github.com/uneasymusings/dj-library-tool/releases) in place of the pinned URL. If `djlib` is not found, `uv tool dir --bin` shows where it is and `uv tool update-shell` adds it to PATH (open a new terminal). Use this persistent install for MCP, not an ephemeral `uvx` environment.

FFmpeg/ffprobe are needed for compressed audio and downloads; YouTube also needs Deno or Node 22+. `djlib doctor` checks them, rekordbox and the Accessibility permission. rekordbox automation needs macOS and the terminal app allowed under System Settings > Privacy & Security > Accessibility.

## Claude Code or Codex

The plugin adds the MCP server and this skill:

```bash
claude plugin marketplace add uneasymusings/dj-library-tool
claude plugin install djlib@dj-library-tool

codex plugin marketplace add uneasymusings/dj-library-tool
codex plugin add djlib@dj-library-tool
```

Or register the server by hand (`--scope user` makes it available in every project):

```bash
claude mcp add --scope user --transport stdio djlib -- "$(command -v djlib)" mcp serve
codex mcp add djlib -- "$(command -v djlib)" mcp serve
```

Restart the host and call `djlib_capabilities`. The server starts even before `djlib init`; its tools then answer `WORKSPACE_REQUIRED` until the workspace exists. It serves the core tools by default; set `DJLIB_MCP_TOOLS=full` in the server's environment for delivery, organize, plan and export tools too.

For a separate trial session without changing host settings: `djlib setup-agent --output ~/djlib-session`, then `python3 ~/djlib-session/launch.py claude` (or `codex`; `python` on Windows).

## Windows

MCP on Windows never starts the background service inside the host. The generated `launch.py` starts it first. For the plugin or a manual registration, run `djlib service start` in an ordinary terminal, then open or reconnect the host. `COORDINATOR_START_REQUIRED` means that step is missing; don't retry the tool as if it would start the service.

## Updates

Stop the service (`djlib service stop`), install the newer wheel URL with `uv tool install --force …`, then restart the host. Keep the existing workspace; don't run `init` on a new folder. Schema upgrades back up the catalog first (`backups/` in the workspace). `COORDINATOR_VERSION_MISMATCH` means an old service is still running: `djlib service stop`, then retry. Generated sessions point at an interpreter path; create a new one after reinstalling.

`uv tool uninstall dj-library-tool` removes the engine but not the workspace or music.
