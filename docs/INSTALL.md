# Install and use the GitHub release

The [v0.1.0a9 release assets](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a9) comprise a Python wheel, source archive, standalone skill ZIP and `SHA256SUMS`. The wheel contains the CLI, 40-tool MCP server, database migrations and full skill. You don't need a Git clone, our developer directories, or a PyPI account.

These commands are pinned to **v0.1.0a9**. Check [status](STATUS.md) for publication, CI and public-installation evidence before using the asset URLs. The [coverage audit](COVERAGE.md) preserves earlier a3/a2 results separately.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/). The install command requests Python 3.13; uv can download it when absent.
- An installed, signed-in Codex or Claude Code for conversational use.
- FFmpeg and ffprobe for compressed music and web acquisition.
- Supported Deno or Node 22+ for YouTube extraction. The download extra includes yt-dlp/EJS; it does not install native decoders or a JavaScript runtime.

## Install the complete toolkit

```bash
uv tool install --python 3.13 'dj-library-tool[download] @ https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a9/dj_library_tool-0.1.0a9-py3-none-any.whl'
djlib version
```

This installs into uv's persistent tool environment. If your shell cannot find `djlib`, inspect `uv tool dir --bin` and add that directory to PATH following uv's instructions. `uv tool update-shell` can configure your shell; open a fresh terminal afterward. [uv tool installation](https://docs.astral.sh/uv/guides/tools/#installing-tools).

The GitHub wheel URL is pinned to a version. Runtime dependencies come from their normal package registry. The source checkout's `uv.lock` remains the exact contributor/CI dependency record. `SHA256SUMS` allows verification of downloaded release artifacts.

## Create your library and assistant session

Use an existing music folder, an empty workspace, and a new session directory. The workspace holds media/catalog/runtime data; the session holds skills and MCP configuration. They should be separate.

macOS/Linux:

```bash
djlib --workspace "$HOME/djlib-library" init --allow-root "$HOME/Music"
djlib --workspace "$HOME/djlib-library" doctor
djlib --workspace "$HOME/djlib-library" setup-agent --output "$HOME/djlib-session"
python3 "$HOME/djlib-session/launch.py" codex
```

Windows PowerShell:

```powershell
djlib --workspace "$env:USERPROFILE\djlib-library" init --allow-root "$env:USERPROFILE\Music"
djlib --workspace "$env:USERPROFILE\djlib-library" doctor
djlib --workspace "$env:USERPROFILE\djlib-library" setup-agent --output "$env:USERPROFILE\djlib-session"
python "$env:USERPROFILE\djlib-session\launch.py" codex
```

Replace the example music folder with your actual path. Supply additional `--allow-root` folders at first initialization if needed. Replace `codex` with `claude` for Claude Code. The launcher uses the engine's installed Python for MCP; your launching Python only needs the standard library.

The generated session contains `.agents/skills/dj-library`, `.claude/skills/dj-library`, `mcp.json`, `session.json`, and `launch.py`. It does not edit personal host configuration or copy credentials. Codex interactive retains your host settings; Claude uses only the explicit MCP configuration. Keep project setting sources enabled for Claude skill discovery. See [agent setup](AGENTS.md).

On Windows, the generated `launch.py` starts the coordinator before launching Codex or Claude. Run it from an ordinary terminal, outside an already running assistant host. For manual MCP configuration, first run this in that external terminal:

```powershell
djlib --workspace "C:\absolute\path\dj-library" service start
```

Then open or reconnect the assistant host. Windows MCP intentionally returns `COORDINATOR_START_REQUIRED` if no coordinator is running. Starting one from inside MCP can tie it to the host SDK's Windows Job Object and terminate it on disconnect; the engine does not bypass that lifecycle boundary. `service status` inspects it, and `service stop` explicitly stops it.

Coordinator startup waits up to 30 seconds for authenticated readiness, with a 45-second lock for concurrent starters. A startup error does not mean a music job was accepted:

| Error | Recovery |
| --- | --- |
| `SERVICE_START_BUSY` | Another client holds the startup lock. Read `service status`, then retry startup if needed. |
| `SERVICE_START_FAILED` | The spawned process exited and no healthy service was found. Inspect the workspace's `runtime/service.log`, resolve its concrete error, then retry. |
| `SERVICE_START_TIMEOUT` | No healthy service appeared within the window; the startup process was still running. Check `service status` and the log before retrying. Do not repeatedly launch new clients or assume the process stopped. |

Redact private paths from logs before sharing. Once startup succeeds, use the original intent/key for any separately uncertain submission; startup errors themselves do not submit that intent.

First prompt for a small real-music success:

> Use the dj-library skill and connected tools. Check capabilities, scan my allowed folder, and prepare a three-track Warm Groove collection for Serato [or rekordbox] from music I already own. Check exact recordings and versions first. Help me import and analyze the working copies, inspect membership and loading, then report what passed and what remains unchecked. I do not know my player model yet.

Give set links, an artist scope or a longer song list after this small trial. The assistant creates the structured requests and keeps unknown IDs, exact versions, source uncertainty and failed items visible. Supply a USB volume and exact player model only when moving to device delivery; app preparation can proceed without them.

The workflow still requires native Serato import and rekordbox device-export steps. Explicit tags, annotations and filtering are supported; automatic acoustic identification, Soulseek, inferred genre/BPM/key/energy, native app automation, and independently verified player-ready USB export remain planned. Installation does not make those features available. See [status](STATUS.md).

## Prepare an app before choosing a player

Use `rekordbox_import` or `serato_import` when the immediate destination is the app's library. These workflows need accepted collection IDs and the installed app version, not a USB or exact player/controller. Use actual collection IDs and confirm the version before creating `app-trial.json`.

Read the version from the app's **About** screen or a supported native XML snapshot before planning. Bundle metadata such as macOS `CFBundleVersion` can be a build number and need not match the app/export version. Keep a mismatch visible; do not manufacture matching observations later.

```json
{
  "name": "App import trial",
  "collection_ids": ["COLLECTION_ID"],
  "workflow": "rekordbox_import",
  "app_version": "7.2.8",
  "audio_mode": "preserve",
  "phase": "pilot",
  "pilot_size": 3
}
```

```bash
djlib --workspace /absolute/path/dj-workspace delivery plan --file app-trial.json
djlib --workspace /absolute/path/dj-workspace delivery prepare DELIVERY_ID \
  --revision CURRENT_REVISION --key app-trial-v1
```

Substitute the installed version; 7.2.8 is an example. For Serato, use `serato_import` and its actual version. Preparation creates separate app working copies and membership artifacts. Wait for the preparation job, perform native import/analysis and record both observations, then use `delivery verify-app DELIVERY_ID --revision CURRENT_REVISION`. In a6, passed analysis observations and app verification also return jobs: use `jobs wait JOB_ID --timeout 30`, then inspect outcomes and the evidence-commit receipt. Read `delivery get` for saved app requirements and `evidence.app_readback.checked_at`; the job itself returns no readiness flag. Public a3 runs those checks synchronously. Follow [DJ delivery](DJ_DELIVERY.md) for the sequence and evidence limits. Standalone rekordbox USB export is a separate `rekordbox_usb` delivery and still requires a player profile.

## Try without personal music

Use a different new workspace:

```bash
djlib --workspace /absolute/path/new-demo-library demo
djlib --workspace /absolute/path/new-demo-library setup-agent --output /absolute/path/new-demo-session
```

The demo creates three original tones and export artifacts. It needs no music accounts. [Validation](VALIDATION.md) describes the actual host/provider/app checks and the remaining hardware gate.

## Install only the skill

Download [dj-library-skill-0.1.0a9.zip](https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a9/dj-library-skill-0.1.0a9.zip) and extract it. Copy the `dj-library` directory, including `references/`, into `.agents/skills/` for a Codex project or `.claude/skills/` for a Claude project. Preserve an existing skill installation rather than overwriting it blindly.

The standalone skill includes [engine installation instructions](../skills/dj-library/references/install.md). It can guide the assistant's workflow, but file acquisition/catalog operations require the installed engine. The complete toolkit install plus `setup-agent` performs this skill installation automatically.

## Update or remove

Before replacing the engine, stop each workspace coordinator with `djlib --workspace PATH service stop`. Install the newer release's explicit URL, then rerun `setup-agent` into a new session if the installation/interpreter path changed. Keep your existing library workspace; do not reinitialize it as a new library.

Version a6 adds `roots list` and `roots add /actual/music/folder` for explicit additive access. Repeating `init --allow-root` with a new root returns `ROOTS_NOT_UPDATED`; it does not silently ignore the change. No music moves and existing permissions remain. Before a schema upgrade, a6 automatically makes an integrity-checked catalog backup under the workspace's `backups/` directory. This covers the engine database only; retain separate backups of music and native app data. Automatic restore is not implemented.

If a new CLI finds an older running coordinator, ordinary requests return `COORDINATOR_VERSION_MISMATCH` before any requested operation is sent. Read `capabilities` to inspect its version, use `service stop` for that workspace, then retry with the new engine. The tool does not replace an old service or resubmit work automatically.

`uv tool uninstall dj-library-tool` removes the tool installation, not your music workspace. A session pointing at an uninstalled interpreter must be regenerated after installation. Avoid ephemeral `uvx` installations for persistent MCP setups.
