# Install

**Easiest:** paste this to Claude Code or Codex: “Set up djlib for me by following https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/SETUP.md”.

**Terminal (Mac or Linux):**

```bash
curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh
```

It installs uv if needed, the newest djlib with Shazam listening and downloads, FFmpeg through Homebrew, and the djlib plugin for Claude Code and Codex if you have them. Then it asks where your music is (drag the folder in, or press Enter for `~/Music`), indexes it in place and runs `djlib doctor`. Nothing is moved, renamed or retagged. Run it again any time to update.

Options go before `sh`, for example `curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | DJLIB_PLUGIN=0 sh`:

- `DJLIB_MUSIC=FOLDER`: index this folder without asking (`DJLIB_MUSIC=` skips it).
- `DJLIB_PLUGIN=0`: don't add the plugin.
- `DJLIB_VERSION=0.1.0a16`: install this release.

djlib needs Python 3.12, which uv brings. Driving rekordbox needs a Mac; checking sets, crates and downloads also work on Linux and Windows. Releases are on the [releases page](https://github.com/uneasymusings/dj-library-tool/releases).

## By hand, and on Windows

1. **FFmpeg** (for MP3, AAC, FLAC, downloads and listening to sets): `brew install ffmpeg` on a Mac, `sudo apt install ffmpeg` (or your distribution's package) on Linux, `winget install Gyan.FFmpeg` on Windows. YouTube downloads also need [Deno](https://deno.com) or Node 22+.
2. **djlib**, with [uv](https://docs.astral.sh/uv/getting-started/installation/):

   ```bash
   uv tool install --python 3.12 'dj-library-tool[download,recognize] @ https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a16/dj_library_tool-0.1.0a16-py3-none-any.whl'
   djlib init ~/Music    # Windows: djlib init "$env:USERPROFILE\Music"
   djlib scan
   djlib doctor
   ```

   `download` adds YouTube and SoundCloud downloads; `recognize` adds Shazam listening. Python 3.12 because song recognition has ready-made Mac and Windows packages there, not yet on 3.13. If `djlib` isn't found, run `uv tool update-shell` and open a new terminal.
3. **Mac: let djlib drive rekordbox.** System Settings → Privacy & Security → Accessibility → turn on the app you run djlib in (Terminal, iTerm, VS Code or Claude). `djlib doctor` checks it.

More music on another drive? `djlib init '/Volumes/DJ Drive/Music'` adds the folder to your setup; then run `djlib scan`.

## Claude Code and Codex

The installer adds the djlib plugin when Claude Code or Codex is installed. To add it yourself, run these and restart the app:

```bash
# Claude Code
claude plugin marketplace add uneasymusings/dj-library-tool
claude plugin install djlib@dj-library-tool

# Codex
codex plugin marketplace add uneasymusings/dj-library-tool
codex plugin add djlib@dj-library-tool
```

Without the plugin, register the server yourself (`--scope user` makes it available in every Claude Code project) and copy the skill as described in [agent setup](AGENTS.md):

```bash
claude mcp add --scope user --transport stdio djlib -- "$(command -v djlib)" mcp serve
codex mcp add djlib -- "$(command -v djlib)" mcp serve
```

The server offers the everyday tools by default; `DJLIB_MCP_TOOLS=full` adds deliveries, notes and filters. To try djlib in a separate session without touching your settings: `djlib setup-agent --output ~/djlib-session`, then `python3 ~/djlib-session/launch.py claude`.

## Song recognition

`djlib identify` (and `djlib set 'LINK'` when a set's tracklist is missing, has IDs or names too few songs for the set) listens to a set with Shazam. It needs no account, but djlib uses Shazam's service the way the Shazam app does, without an official API, so it may slow down or stop working. An hour-long set usually takes 5–10 minutes; when Shazam is busy it can take half an hour. For a licensed service, use your own account and set its keys before djlib's background service starts:

```bash
export DJLIB_RECOGNIZER=audd DJLIB_AUDD_TOKEN=YOUR_TOKEN                # AudD
export DJLIB_RECOGNIZER=acrcloud DJLIB_ACRCLOUD_HOST=identify-eu-west-1.acrcloud.com \
  DJLIB_ACRCLOUD_KEY=YOUR_KEY DJLIB_ACRCLOUD_SECRET=YOUR_SECRET          # or ACRCloud
djlib service stop                                                    # the next command starts it with them
```

Put the `export` lines in your shell profile (for example `~/.zshrc`) to keep them. `djlib identify 'LINK' --recognizer audd` picks a service for one run; its keys must still be set. `djlib doctor` shows which service is used and what is missing.

Reading the songs you Shazamed (`djlib shazam status`) needs none of this. If macOS keeps your Shazam history private, allow the app you run djlib in (Terminal, iTerm, VS Code or Claude) under System Settings → Privacy & Security → Full Disk Access, quit and reopen it.

## Update

`djlib upgrade` updates djlib and the Claude Code plugin, and restarts the background service. `djlib upgrade --check` only says whether a newer djlib exists. Rerunning the one-line installer does the same, Codex plugin included. Restart your assistant afterwards.

- **Coming from 0.1.0a15?** Its upgrade runs one last time: a quiet wait, and it can end with a Python error (`ModuleNotFoundError`). The upgrade worked: run `djlib doctor` to confirm.
- **Coming from 0.1.0a14?** Run `djlib upgrade` twice. The second run adds song recognition.

**Your sets after upgrading to 0.1.0a16.** Checking a set again updates its saved list. If 0.1.0a16 finds more songs you own (it now also matches a tracklist's spelling of a song Shazam heard under other credits), it builds a new crate, and pushing it gives a “Name (2)” playlist in rekordbox next to the old one: delete the older one. A tracklist file whose untimed ID lines 0.1.0a15 skipped also becomes a new set. A set you check again with a tracklist pasted from 1001Tracklists stays the same set (its spellings, such as “ft.” for “Feat.”, don't count). Shazam sets are now named after their days (“Shazam 26 Sep – 12 Oct”), so an older Shazam set checked again becomes a new set under the new name.

By hand: stop the background service, then install the newer release over the old one. Your setup, djlib's folder (its sets and crates) and your music stay as they are.

```bash
djlib service stop
uv tool install --force --python 3.12 'dj-library-tool[download,recognize] @ NEW_WHEEL_URL'
```

The commands are the same in macOS Terminal, Linux shells and Windows PowerShell. Regenerate any `setup-agent` session afterwards.

## Uninstall

```bash
djlib service stop
uv tool uninstall dj-library-tool
```

This removes djlib only. To remove its sets, crates and index of your music too, delete the folder `djlib init` created (by default `~/.local/share/djlib/default`; Windows `%USERPROFILE%\.local\share\djlib\default`) and the settings folder (`~/.config/djlib`; Windows `%APPDATA%\djlib`). Your music is never moved or changed. On a Mac, you can also turn the app off again under Accessibility.

Problems with PATH, the background service, Windows or upgrades: [troubleshooting](TROUBLESHOOTING.md).
