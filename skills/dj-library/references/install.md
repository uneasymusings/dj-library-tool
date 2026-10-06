# Install the engine and tools from GitHub

The installation command targets the [v0.1.0a11 GitHub release assets](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a11), including the engine, complete matching skill and 40 MCP tools. Check the release page and [validation status](https://github.com/uneasymusings/dj-library-tool/blob/main/docs/STATUS.md) for publication and public-installation evidence. Use the skill packaged with the installed version and check capabilities; older a3 lacks discovery/reconciliation and queued native checks. No checkout or PyPI release is required. Python runs locally to access music and mounted storage.

The earlier `0.1.0a2` engine does not include the `delivery`, `requests` or `organize` commands in this skill. Check `version`, `capabilities` and the connected schemas for a3's `targeted_delivery_workflow`, `app_import_workflow`, `owned_request_matching`, `missing_track_ledger`, `catalog_annotations`, `organization_filters` and native XML inspection. Preserve existing installations/configuration; use a separate persistent environment for isolated trials. Before an intentional upgrade, stop each workspace coordinator and regenerate the session if its interpreter path changes.

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```bash
uv tool install --python 3.13 'dj-library-tool[download] @ https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a11/dj_library_tool-0.1.0a11-py3-none-any.whl'
djlib version
```

If `djlib` is not found, use `uv tool dir --bin` to locate the installed executable or follow uv's PATH instructions. This is a persistent tool installation: do not use an ephemeral `uvx` environment to generate persistent MCP configuration. Removing or relocating the installation breaks its configured interpreter path; rerun setup into a new session after reinstalling.

Initialize a new library workspace with the user's actual music folders, then create a separate agent session:

```bash
djlib --workspace /absolute/path/dj-library init --allow-root /absolute/path/music
djlib --workspace /absolute/path/dj-library setup-agent --output /absolute/path/dj-session
```

The session includes both project skills, explicit MCP configuration, and `launch.py`. Run that launcher with an available Python (`python3` on many Macs, `python` on Windows) and `codex` or `claude`. The chosen host must already be installed and signed in. Personal host configuration is not edited.

On Windows, run `launch.py` from an external terminal so it starts the coordinator before the AI host. For manual MCP configuration, first run `djlib --workspace PATH service start` in that terminal, then open/reconnect the host. Windows MCP returns `COORDINATOR_START_REQUIRED` on cold start; do not retry it as if it starts a service. A coordinator spawned inside the MCP SDK's Windows Job Object can be terminated during host cleanup. The engine preserves that lifecycle boundary and does not escape the job. Use the matching a6 launcher; existing generated sessions are not rewritten by an upgrade.

Since a6, inspect `roots list` and use `roots add PATH` to explicitly add user-authorized existing folders. Repeating initialization with a new root returns `ROOTS_NOT_UPDATED`. Schema upgrades make integrity-checked catalog backups before migrating; this does not back up music/native app databases or provide automatic restore. Preserve the existing workspace and create a new session when upgrading. An app-only first trial needs owned tracks and the actual app version, not a player model or USB.

FFmpeg/ffprobe are separate prerequisites for compressed audio and downloads; YouTube also needs supported Deno or Node 22+. Check `djlib --workspace PATH doctor`. The download extra installs yt-dlp/EJS, not native decoders. The tool's current app/device limits still apply after installation.
