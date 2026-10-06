# Use from Claude Code or Codex

The engine is model-independent. Conversation, web search and interpretation belong to your assistant; everything that touches your music, rekordbox or a USB stick is done by `djlib` on your computer. The [dj-library skill](../skills/dj-library/SKILL.md) teaches the assistant the workflow, and the MCP server gives it typed tools.

First [install the engine](INSTALL.md) and run `djlib init --allow-root ~/Music` and `djlib scan`.

## Plugin (recommended)

The repository is a plugin marketplace for both hosts. The plugin registers the MCP server (`djlib mcp serve`) and installs the skill.

```bash
# Claude Code
claude plugin marketplace add uneasymusings/dj-library-tool
claude plugin install djlib@dj-library-tool

# Codex
codex plugin marketplace add uneasymusings/dj-library-tool
codex plugin add djlib@dj-library-tool
```

Restart the host. The plugin runs `djlib` from your PATH, so install the engine with `uv tool install` first. Updating the engine doesn't update the plugin, or the other way round. To refresh the plugin, run `claude plugin marketplace update dj-library-tool` and then `claude plugin update djlib@dj-library-tool`, or `codex plugin marketplace upgrade`.

## Manual setup

Register the server yourself. `--scope user` makes it available in every Claude Code project:

```bash
claude mcp add --scope user --transport stdio djlib -- "$(command -v djlib)" mcp serve
codex mcp add djlib -- "$(command -v djlib)" mcp serve
```

On Windows, use the path from `(Get-Command djlib).Source`. These commands write your host's MCP configuration. Add `--workspace PATH` before `mcp serve` only for a workspace other than the one `djlib init` remembered.

Then copy the skill, keeping its `references/` folder: `skills/dj-library` into `~/.claude/skills/` (Claude Code) or `~/.agents/skills/` (Codex), or the project's `.claude/skills/` or `.agents/skills/`. The release also has a standalone `dj-library-skill-VERSION.zip`. Don't overwrite an existing skill blindly; compare first.

## Separate trial session

To try djlib without touching your host settings:

```bash
djlib setup-agent --output ~/djlib-session
python3 ~/djlib-session/launch.py claude    # or codex; on Windows: python ...\launch.py
```

The output folder must not exist. It gets both skill copies, an explicit `mcp.json` and `launch.py`, which starts the background service and then the host with only this MCP configuration (Claude: `--strict-mcp-config`; Codex: `-c` overrides). No credentials or personal settings are copied or changed. For an isolated non-interactive Codex run, `python3 launch.py codex exec --ephemeral --skip-git-repo-check ...` ignores personal config.

## Tools

The server serves the core tools by default: capabilities, library, folders, scans, request lists, crates, set links (`djlib_source_inspect`), web search (`djlib_find_sources`), downloads, jobs and rekordbox analysis. Set `DJLIB_MCP_TOOLS=full` in the server's environment to add plans, exports, reviews, notes and filters, reconciliation and working-copy deliveries:

```bash
claude mcp add --scope user --transport stdio djlib -e DJLIB_MCP_TOOLS=full -- "$(command -v djlib)" mcp serve
codex mcp add djlib --env DJLIB_MCP_TOOLS=full -- "$(command -v djlib)" mcp serve
```

- Every tool returns the CLI's JSON envelope. Check `ok` and `error`, even when the host reports the call as successful. Text content is the same JSON as `structuredContent`, compact.
- The server starts without a workspace; its tools then answer `WORKSPACE_REQUIRED` with the `djlib init` command to run.
- Mutating tools take idempotency keys, and revisions guard stale choices. Download tools contact YouTube, SoundCloud or Bandcamp. No tool changes rekordbox's database or a USB device.
- **rekordbox and USB steps are CLI-only** (`djlib set`, `djlib rekordbox push|usb|pull`, macOS). The skill tells the assistant to run them in the background and to tell you which playlist to click. In JSON mode the click prompt also appears on stderr as `{"event": "select_playlist", …}`.
- On Windows, start the service with `djlib service start` in an ordinary terminal before connecting; see [troubleshooting](TROUBLESHOOTING.md#windows-and-mcp).

## Example requests

- “Here's tonight's tracklist. Check what I own, tell me what's missing and which other versions I have, and put the set on my USB through rekordbox.”
- “Here's a SoundCloud set. Get its tracklist, tell me what listeners said about the IDs, and download the missing songs you're sure about.”
- “Put my Friday crate into rekordbox once I've stepped away from the computer.”
- “Find my saved Warm Groove crates and tell me which ones are on my stick.”

The assistant should report owned and missing songs, other versions you own, unknown IDs with listeners' guesses, download quality, and what the USB check found. Listener comments and publisher text are hints, not identifications, and no djlib tool recognizes audio.

The host's own permissions still apply. The service is a local single-user tool; see the [security model](../SECURITY.md).
