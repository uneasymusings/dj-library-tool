# Install and use the GitHub release

The [v0.1.0a3 release assets](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a3) comprise a Python wheel, source archive, standalone skill ZIP, and `SHA256SUMS`. The wheel contains the CLI, 34-tool MCP server, database migrations, and full skill. You don't need a Git clone, our developer directories, or a PyPI account.

These commands are pinned to **v0.1.0a3**. Check [status](STATUS.md) for publication and validation state. The [coverage audit](COVERAGE.md) distinguishes this version's app-first delivery, request tracking and organization from the historical a2 baseline.

## Prerequisites

- [uv](https://docs.astral.sh/uv/getting-started/installation/). The install command requests Python 3.13; uv can download it when absent.
- An installed, signed-in Codex or Claude Code for conversational use.
- FFmpeg and ffprobe for compressed music and web acquisition.
- Supported Deno or Node 22+ for YouTube extraction. The download extra includes yt-dlp/EJS; it does not install native decoders or a JavaScript runtime.

## Install the complete toolkit

```bash
uv tool install --python 3.13 'dj-library-tool[download] @ https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a3/dj_library_tool-0.1.0a3-py3-none-any.whl'
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

First prompt:

> Use the dj-library skill and connected tools. Read capabilities and check prerequisites. Scan my allowed music folder, then find these exact songs/versions in my existing catalog: [list]. Inspect these set links for published tracklists: [links]. Use available search tools to find missing individual recording sources. Organize accepted tracks into named collections, report unknown IDs and conflicts, and prepare app import artifacts. My USB is [mount path] and my player/controller is [model]. Report downloaded, imported, and device-exported states separately.

The workflow still requires native Serato import and rekordbox device-export steps. Explicit tags, annotations and filtering are supported; automatic acoustic identification, Soulseek, inferred genre/BPM/key/energy, native app automation, and independently verified player-ready USB export remain planned. Installation does not make those features available. See [status](STATUS.md).

## Prepare an app before choosing a player

Use `rekordbox_import` or `serato_import` when the immediate destination is the app's library. These workflows need accepted collection IDs and the installed app version, not a USB or exact player/controller. For example, save an actual-version request as `app-trial.json`:

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

Substitute the installed version; 7.2.8 is an example. For Serato, use `serato_import` and its actual version. Preparation creates separate app working copies and membership artifacts. Perform native import/analysis, record both observations, then use `delivery verify-app DELIVERY_ID --revision CURRENT_REVISION`. Follow [DJ delivery](DJ_DELIVERY.md) for the exact sequence and evidence limits. Standalone rekordbox USB export is a separate `rekordbox_usb` delivery and still requires a player profile.

## Try without personal music

Use a different new workspace:

```bash
djlib --workspace /absolute/path/new-demo-library demo
djlib --workspace /absolute/path/new-demo-library setup-agent --output /absolute/path/new-demo-session
```

The demo creates three original tones and export artifacts. It needs no music accounts. [Validation](VALIDATION.md) describes the actual host/provider/app checks and the remaining hardware gate.

## Install only the skill

Download [dj-library-skill-0.1.0a3.zip](https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a3/dj-library-skill-0.1.0a3.zip) and extract it. Copy the `dj-library` directory, including `references/`, into `.agents/skills/` for a Codex project or `.claude/skills/` for a Claude project. Preserve an existing skill installation rather than overwriting it blindly.

The standalone skill includes [engine installation instructions](../skills/dj-library/references/install.md). It can guide the assistant's workflow, but file acquisition/catalog operations require the installed engine. The complete toolkit install plus `setup-agent` performs this skill installation automatically.

## Update or remove

Before replacing the engine, stop each workspace coordinator with `djlib --workspace PATH service stop`. Install the newer release's explicit URL, then rerun `setup-agent` into a new session if the installation/interpreter path changed. Keep your existing library workspace; do not reinitialize it as a new library.

If a new CLI finds an older running coordinator, ordinary requests return `COORDINATOR_VERSION_MISMATCH` before any requested operation is sent. Read `capabilities` to inspect its version, use `service stop` for that workspace, then retry with the new engine. The tool does not replace an old service or resubmit work automatically.

`uv tool uninstall dj-library-tool` removes the tool installation, not your music workspace. A session pointing at an uninstalled interpreter must be regenerated after installation. Avoid ephemeral `uvx` installations for persistent MCP setups.
