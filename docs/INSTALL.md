# Install

djlib runs locally with Python 3.12, installed through [uv](https://docs.astral.sh/uv/getting-started/installation/). Driving rekordbox needs macOS; the catalog, tracklist checks, crates and downloads also work on Linux and Windows. Releases are on the [releases page](https://github.com/uneasymusings/dj-library-tool/releases); the commands below use v0.1.0a14.

On macOS or Linux, one line installs uv if missing, the newest release (with downloads and song recognition), FFmpeg (through Homebrew when available) and the djlib plugin for Claude Code and Codex when they're installed; in a terminal it then asks where your music is, indexes it and runs `djlib doctor`: `curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh`. `DJLIB_PLUGIN=0` skips the plugin, `DJLIB_MUSIC=FOLDER` indexes a folder without asking (`DJLIB_MUSIC=` skips that step) and `DJLIB_VERSION=0.1.0a14` pins a release. With an assistant, paste: “Set up djlib for me by following https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/SETUP.md”.

1. **FFmpeg** (for MP3, AAC, FLAC, downloads and listening to sets): `brew install ffmpeg` on macOS, `sudo apt install ffmpeg` (or your distribution's package) on Linux, `winget install Gyan.FFmpeg` on Windows. YouTube downloads also need [Deno](https://deno.com) or Node 22+.
2. **djlib**:

   ```bash
   uv tool install --python 3.12 'dj-library-tool[download,recognize] @ https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a15/dj_library_tool-0.1.0a15-py3-none-any.whl'
   djlib init --allow-root ~/Music    # Windows: --allow-root "$env:USERPROFILE\Music"
   djlib scan
   djlib doctor
   ```

   `download` adds YouTube and SoundCloud; `recognize` adds song recognition for `djlib identify`. Python 3.12 because song recognition has ready-made macOS and Windows packages there, not yet on 3.13. If `djlib` is not found, run `uv tool update-shell` and open a new terminal.
3. **macOS: allow rekordbox automation.** System Settings → Privacy & Security → Accessibility → turn on the app you run djlib from (Terminal, iTerm, or the app your assistant runs in). `djlib doctor` checks it, and djlib asks for it with `APP_AUTOMATION_NOT_ALLOWED` when it's missing.
4. **Assistant (optional):** add the plugin to Claude Code or Codex; see [agent setup](AGENTS.md).

## Song recognition

`djlib identify` (and `djlib set LINK` when a set's tracklist is missing or has IDs) listens to a set with Shazam by default. It needs no account, but djlib uses Shazam's service the way the Shazam app does and Apple doesn't offer it to other apps, so it may slow down or stop working. For a licensed service, use your own account and set its keys before djlib's background service starts:

```bash
export DJLIB_RECOGNIZER=audd DJLIB_AUDD_TOKEN=YOUR_TOKEN                # AudD
export DJLIB_RECOGNIZER=acrcloud DJLIB_ACRCLOUD_HOST=identify-eu-west-1.acrcloud.com \
  DJLIB_ACRCLOUD_KEY=YOUR_KEY DJLIB_ACRCLOUD_SECRET=YOUR_SECRET          # or ACRCloud
djlib service stop                                                    # the next command starts it with them
```

Put the `export` lines in your shell profile (for example `~/.zshrc`) to keep them. `djlib identify LINK --recognizer audd` picks a service for one run; its keys must still be set. `djlib doctor` shows which service is used and what is missing. While listening, djlib keeps each set's page, what it heard and the tracklist in the workspace's `sets/` folder; the audio is deleted afterwards unless `DJLIB_KEEP_SET_AUDIO=1`.

Reading the songs you Shazamed (`djlib set shazam`) needs none of this. On a Mac, `djlib shazam status` shows whether djlib finds your history and how to turn on Shazam's iCloud sync; if macOS keeps it private, allow your terminal app under System Settings → Privacy & Security → Full Disk Access.

## Update

`djlib upgrade` installs the newest release (with song recognition: run it again on the newest release when `djlib doctor` says song recognition isn't installed, as after upgrading from 0.1.0a14), restarts the background service and updates the Claude Code plugin (rerunning the one-line installer does the same, Codex plugin included). By hand: stop the background service, then install the newer release's wheel URL over the old one. Your workspace, catalog and music stay as they are.

```bash
djlib service stop
uv tool install --force --python 3.12 'dj-library-tool[download,recognize] @ NEW_WHEEL_URL'
```

The commands are the same in macOS Terminal, Linux shells and Windows PowerShell. Restart your assistant afterwards, and regenerate any `setup-agent` session.

## Uninstall

```bash
djlib service stop
uv tool uninstall dj-library-tool
```

This removes the engine only. To remove the catalog too, delete the workspace folder you created with `djlib init` (by default `~/.local/share/djlib/default`; Windows `%USERPROFILE%\.local\share\djlib\default`) and the settings folder (`~/.config/djlib`; Windows `%APPDATA%\djlib`). Your music is never moved or changed. On macOS, you can also turn the terminal off again under Accessibility.

Problems with PATH, the background service, Windows MCP or upgrades: [troubleshooting](TROUBLESHOOTING.md).
