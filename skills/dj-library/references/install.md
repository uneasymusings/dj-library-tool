# Install the engine and tools from GitHub

The installation command targets the [v0.1.0a3 GitHub release assets](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a3), including the engine, this complete skill and the 34-tool MCP server. Consult the release page for asset availability; do not infer publication from this skill's version. No project checkout or PyPI release is required. Python runs locally to access the user's music and mounted storage.

The earlier `0.1.0a2` engine does not include the `delivery`, `requests` or `organize` commands in this skill. Check `version`, `capabilities` and the connected schemas for a3's `targeted_delivery_workflow`, `app_import_workflow`, `owned_request_matching`, `missing_track_ledger`, `catalog_annotations`, `organization_filters` and native XML inspection. Preserve existing installations/configuration; use a separate persistent environment for isolated trials. Before an intentional upgrade, stop each workspace coordinator and regenerate the session if its interpreter path changes.

With [uv](https://docs.astral.sh/uv/getting-started/installation/) installed:

```bash
uv tool install --python 3.13 'dj-library-tool[download] @ https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a3/dj_library_tool-0.1.0a3-py3-none-any.whl'
djlib version
```

If `djlib` is not found, use `uv tool dir --bin` to locate the installed executable or follow uv's PATH instructions. This is a persistent tool installation: do not use an ephemeral `uvx` environment to generate persistent MCP configuration. Removing or relocating the installation breaks its configured interpreter path; rerun setup into a new session after reinstalling.

Initialize a new library workspace with the user's actual music folders, then create a separate agent session:

```bash
djlib --workspace /absolute/path/dj-library init --allow-root /absolute/path/music
djlib --workspace /absolute/path/dj-library setup-agent --output /absolute/path/dj-session
```

The session includes both project skills, explicit MCP configuration, and `launch.py`. Run that launcher with an available Python (`python3` on many Macs, `python` on Windows) and `codex` or `claude`. The chosen host must already be installed and signed in. Personal host configuration is not edited.

FFmpeg/ffprobe are separate prerequisites for compressed audio and downloads; YouTube also needs supported Deno or Node 22+. Check `djlib --workspace PATH doctor`. The download extra installs yt-dlp/EJS, not native decoders. The tool's current app/device limits still apply after installation.
