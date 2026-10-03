# Use from Codex or Claude Code

The engine is model-independent. Conversation, online search, and interpretation belong to your chosen assistant; durable side effects belong to `djlib`. A skill supplies workflow guidance, and MCP exposes typed tools. Either can be used independently.

## Prerequisites

Clone the repository, run `uv sync --locked` (add `--extra download` for web sources), and initialize a separate library workspace:

```bash
uv run djlib --workspace /absolute/path/dj-workspace init \
  --allow-root /absolute/path/music
```

Use absolute paths in host configuration. Do not point a host at `uv run` with an unspecified working directory; use the installed executable in this checkout's virtual environment.

## Connect MCP

```bash
codex mcp add djlib -- /absolute/path/dj-library-tool/.venv/bin/djlib \
  --workspace /absolute/path/dj-workspace mcp serve
```

```bash
claude mcp add --transport stdio djlib -- /absolute/path/dj-library-tool/.venv/bin/djlib \
  --workspace /absolute/path/dj-workspace mcp serve
```

These commands write your host's MCP configuration. Run them when you want to register the server. Use `.venv\Scripts\djlib.exe` on Windows. Restart or reconnect the host, then inspect its MCP connections and call `djlib_capabilities` first.

The local MCP process speaks stdio only. It discovers or starts the authenticated loopback coordinator for the workspace. Multiple hosts share the same coordinator and catalog. Accepted jobs are designed to survive disconnection; an MCP reconnection does not submit another acquisition.

Host configuration references: [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp), [Claude Code MCP](https://code.claude.com/docs/en/mcp).

## Add workflow guidance

The portable entrypoint is [`skills/dj-library/SKILL.md`](../skills/dj-library/SKILL.md), with one CLI reference. Copy the entire directory to your host's supported skill location, preserving its name and relative reference path.

For Claude Code, from this checkout:

```bash
mkdir -p .claude/skills
cp -R skills/dj-library .claude/skills/dj-library
```

Use `~/.claude/skills/dj-library` instead for personal installation across projects. Do not overwrite an existing skill of the same name; compare and update it deliberately. Invoke `/dj-library` or let the host discover it for relevant requests. See [Claude Code skills](https://code.claude.com/docs/en/skills).

For Codex, the skill creator workflow uses `~/.codex/skills` (or `$CODEX_HOME/skills`) for personal skills. Copy `skills/dj-library` into that directory if your installed Codex host uses it; hosts with an Agent Skills discovery directory should use their documented location. You can always explicitly ask Codex to read the repo's `skills/dj-library/SKILL.md` to use it without changing personal configuration.

CLI-only use is also supported: make the checkout's `djlib` executable available to the host and supply the workspace path. The skill's [CLI recipes](../skills/dj-library/references/cli.md) cover strict request JSON.

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
