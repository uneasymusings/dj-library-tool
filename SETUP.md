# Set up djlib with your AI assistant

Paste this into Claude Code or Codex:

> Set up djlib for me by following https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/SETUP.md

The rest of this page is for the assistant. It takes a few minutes, most of it indexing music.

## Steps for the assistant

1. **Install or update.** Run `djlib --version`. If it's missing, or `djlib upgrade --check` reports `update_available: true`, install with (macOS or Linux; it installs software, so ask first if you should):

   ```bash
   curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh
   ```

   It installs uv if needed, the newest djlib, FFmpeg through Homebrew, and the djlib plugin for Claude Code and Codex when they're installed. Running it again updates everything. On Windows, follow [installation](docs/INSTALL.md) instead. If `djlib` isn't found afterwards, use `~/.local/bin/djlib`.

2. **Index their music.** Ask where their music is (usually `~/Music`; several folders and external drives are fine). Then run `djlib init --allow-root FOLDER` (repeat `--allow-root` per folder) and `djlib scan` in the background. Scanning reads files in place and never moves or retags them; a first scan takes about a quarter of a second per track. `djlib status` shows the count.

3. **rekordbox on a Mac.** Run `djlib doctor`. If `rekordbox_automation_allowed` is false, ask them to turn on the app that runs you (Terminal, iTerm, VS Code or Claude) under System Settings → Privacy & Security → Accessibility, then restart that app. Pushing playlists and USB export need it; checking tracklists doesn't.

4. **Load the plugin.** The installer added it; it takes effect after a restart of Claude Code or Codex (in Claude Code, `/mcp` → djlib → Reconnect also works). Until then, use the `djlib` CLI directly; it does everything.

5. **First set.** Ask for a tracklist (pasted text, a file, or a YouTube/SoundCloud link). Write pasted text to a file with one `Artist - Title (Mix)` per line and a heading line for the set's name, then run `djlib set FILE --no-rekordbox` and report owned, missing and other versions they own. Offer next: rekordbox (`djlib set FILE`), missing songs (`--fetch`, after asking), the USB stick (`--usb`, while they're at the computer). The dj-library skill has the details.

Report back: the djlib version, the music folders and how many tracks were indexed, anything `djlib doctor` flagged, and whether the plugin was added.
