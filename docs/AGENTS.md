# Use from Codex or Claude Code

The engine is model-independent. Conversation, online search, and interpretation belong to your chosen assistant; durable side effects belong to `djlib`. A skill supplies workflow guidance, and MCP exposes typed tools. Either can be used independently.

## Prerequisites

Install the complete engine from the [GitHub release using the public installation guide](INSTALL.md). The package includes the skill and MCP server; it needs no checkout. Initialize a separate library workspace:

```bash
djlib --workspace /absolute/path/dj-workspace init \
  --allow-root /absolute/path/music
```

The session generator records the installed interpreter's absolute path. Use a persistent installation: a transient `uvx` cache or removable developer checkout is unsuitable for persistent MCP configuration. Contributors can still use `uv sync --locked` and their checkout's executable.

## Connect MCP

### Separate trial session

```bash
djlib --workspace /absolute/path/dj-workspace setup-agent \
  --output /absolute/path/dj-session
cd /absolute/path/dj-session
python launch.py codex
python launch.py claude
```

The output folder must not exist. It contains `.agents/skills/dj-library`, `.claude/skills/dj-library`, `mcp.json`, and a cross-platform Python launcher. It uses the installing Python executable rather than depending on the current working directory or `uv`. The skill also ships in the wheel. No credentials or workspace token are copied into the session.

Codex interactive uses your personal settings plus explicit djlib MCP overrides. For an isolated noninteractive run, `python launch.py codex exec --ephemeral --skip-git-repo-check ...` ignores personal config; authentication still uses your existing sign-in. Claude uses `--strict-mcp-config`, so other MCP servers are not loaded; other host settings remain in effect. In Claude, disabling all setting sources also disables project skill discovery. For an isolated test, use `--setting-sources project`, or explicitly ask the host to read the installed `SKILL.md`.

These commands do not change personal host configuration. The generated README contains trial instructions. Existing host approval rules remain active. [Validation](VALIDATION.md) records actual Codex 0.160.0 / Claude Code 2.1.288 trials.

### Persistent registration

```bash
codex mcp add djlib -- /absolute/path/to/installed/djlib \
  --workspace /absolute/path/dj-workspace mcp serve
```

```bash
claude mcp add --transport stdio djlib -- /absolute/path/to/installed/djlib \
  --workspace /absolute/path/dj-workspace mcp serve
```

These commands write your host's MCP configuration. Run them when you want to register the server. Find the installed executable with `command -v djlib` on macOS/Linux or `(Get-Command djlib).Source` in PowerShell, and substitute that absolute path. Restart or reconnect the host, then inspect its MCP connections and call `djlib_capabilities` first.

The local MCP process speaks stdio only. It discovers or starts the authenticated loopback coordinator for the workspace. Multiple hosts share the same coordinator and catalog. Accepted jobs are designed to survive disconnection; an MCP reconnection does not submit another acquisition.

Host configuration references: [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp), [Claude Code MCP](https://code.claude.com/docs/en/mcp).

## Add workflow guidance

The portable entrypoint is [`skills/dj-library/SKILL.md`](../skills/dj-library/SKILL.md), with CLI and installation references. Copy the entire directory to your host's supported skill location, preserving its name and relative reference path.

For Claude Code, from this checkout:

```bash
mkdir -p .claude/skills
cp -R skills/dj-library .claude/skills/dj-library
```

Use `~/.claude/skills/dj-library` instead for personal installation across projects. Do not overwrite an existing skill of the same name; compare and update it deliberately. Invoke `/dj-library` or let the host discover it for relevant requests. See [Claude Code skills](https://code.claude.com/docs/en/skills).

For current Codex, use `.agents/skills/dj-library` in your project or `~/.agents/skills/dj-library` for personal discovery. The generated trial session handles project placement automatically. Older hosts may have a different discovery directory; follow their installed version's documentation. See [Codex skill locations](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills). You can also explicitly ask the host to read the repo's `skills/dj-library/SKILL.md`.

CLI-only use is also supported: make the installed `djlib` executable available to the host and supply the workspace path. The skill's [CLI recipes](../skills/dj-library/references/cli.md) cover strict request JSON.

## Example requests

- “Build a collection from these local paths. Reuse the files in place and show me metadata conflicts.”
- “Read this set's published tracklist. Find individual sources with your search tools, keep unknown IDs in a missing-track report, and queue the recordings I select.”
- “Prepare the accepted collection for rekordbox, show me the import paths, and check capacity at `/Volumes/DJ_USB`.”
- “Resume my previous job and show the failed items. Reuse the original submission key if the previous response was lost.”

The assistant should report source evidence, unresolved tracks, quality uncertainty, and app/device readiness. Requesting “every ID” does not turn publisher metadata into acoustic recognition. The full workflow for “all Joy Orbison's work” is planned; this first release can operate a selected tracklist, but has no catalog enumeration provider.

## Tool behavior

Public tools return the same JSON envelope as the CLI. Check `ok` and `error`, even when the host considers an MCP call successfully transported. Application-level errors are structured results, not necessarily MCP protocol errors.

Read-only hints describe the music/catalog effect. A read can start the local coordinator and migrate the workspace database. Mutating tools expose idempotency keys; plan/review revisions guard stale choices. Download tools are marked as contacting external providers. None of the current tools mutates a native DJ database or USB device.

The host's own execution permissions still apply. The service is a local trusted-user tool, not a remote multi-user authorization system. Review the [security model](../SECURITY.md).
