# Install

djlib runs locally with Python 3.13, installed through [uv](https://docs.astral.sh/uv/getting-started/installation/). Driving rekordbox needs macOS; the catalog, tracklist checks, crates and downloads also work on Linux and Windows. Releases are on the [releases page](https://github.com/uneasymusings/dj-library-tool/releases); the commands below use v0.1.0a12.

On macOS or Linux, one line installs uv if missing, the newest release and FFmpeg (through Homebrew when available), then runs `djlib doctor`; continue with `djlib init` in step 2: `curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh`

1. **FFmpeg** (for MP3, AAC, FLAC and downloads): `brew install ffmpeg` on macOS, `sudo apt install ffmpeg` (or your distribution's package) on Linux, `winget install Gyan.FFmpeg` on Windows. YouTube downloads also need [Deno](https://deno.com) or Node 22+.
2. **djlib**:

   ```bash
   uv tool install --python 3.13 'dj-library-tool[download] @ https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a12/dj_library_tool-0.1.0a12-py3-none-any.whl'
   djlib init --allow-root ~/Music    # Windows: --allow-root "$env:USERPROFILE\Music"
   djlib scan
   djlib doctor
   ```

   If `djlib` is not found, run `uv tool update-shell` and open a new terminal.
3. **macOS: allow rekordbox automation.** System Settings → Privacy & Security → Accessibility → turn on the app you run djlib from (Terminal, iTerm, or the app your assistant runs in). `djlib doctor` checks it, and djlib asks for it with `APP_AUTOMATION_NOT_ALLOWED` when it's missing.
4. **Assistant (optional):** add the plugin to Claude Code or Codex; see [agent setup](AGENTS.md).

## Update

Stop the background service, then install the newer release's wheel URL over the old one (on macOS or Linux, rerunning the one-line installer does this). Your workspace, catalog and music stay as they are.

```bash
djlib service stop
uv tool install --force --python 3.13 'dj-library-tool[download] @ NEW_WHEEL_URL'
```

The commands are the same in macOS Terminal, Linux shells and Windows PowerShell. Restart your assistant afterwards, and regenerate any `setup-agent` session.

## Uninstall

```bash
djlib service stop
uv tool uninstall dj-library-tool
```

This removes the engine only. To remove the catalog too, delete the workspace folder you created with `djlib init` (by default `~/.local/share/djlib/default`; Windows `%USERPROFILE%\.local\share\djlib\default`) and the settings folder (`~/.config/djlib`; Windows `%APPDATA%\djlib`). Your music is never moved or changed. On macOS, you can also turn the terminal off again under Accessibility.

Problems with PATH, the background service, Windows MCP or upgrades: [troubleshooting](TROUBLESHOOTING.md).
