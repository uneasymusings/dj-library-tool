# Set up djlib with your AI assistant

Paste this into Claude Code or Codex:

> Set up djlib for me by following https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/SETUP.md

The rest of this page is for the assistant. It takes a few minutes, most of it indexing music.

## Steps for the assistant

1. **Install or update.** Run `djlib --version`. If it's missing, or `djlib upgrade --check` reports `update_available: true`, install with (macOS or Linux; it installs software, so ask first if you should):

   ```bash
   curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh
   ```

   It installs uv if needed, the newest djlib (with downloads and song recognition), FFmpeg through Homebrew, and the djlib plugin for Claude Code and Codex when they're installed. Running it again updates everything. On Windows, follow [installation](docs/INSTALL.md) instead. If `djlib` isn't found afterwards, use `~/.local/bin/djlib`.

2. **Index their music.** Ask where their music is (usually `~/Music`; several folders and external drives are fine). Then run `djlib init --allow-root FOLDER` (repeat `--allow-root` per folder) and `djlib scan` in the background. Scanning reads files in place and never moves or retags them; a first scan takes about a quarter of a second per track. `djlib status` shows the count.

3. **rekordbox on a Mac.** Run `djlib doctor`. If `rekordbox_automation_allowed` is false, ask them to turn on the app that runs you (Terminal, iTerm, VS Code or Claude) under System Settings → Privacy & Security → Accessibility, then restart that app. Pushing playlists and USB export need it; checking tracklists doesn't.

4. **Load the plugin.** The installer added it; it takes effect after a restart of Claude Code or Codex (in Claude Code, `/mcp` → djlib → Reconnect also works). Until then, use the `djlib` CLI directly; it does everything.

5. **First set.** Ask for a tracklist (pasted text or a file) or a YouTube/SoundCloud link to a set. Write pasted text to a file with one `Artist - Title (Mix)` per line and a heading line for the set's name, then run `djlib set FILE --no-rekordbox`. A link works the same way (`djlib set LINK --no-rekordbox`, in the background): when its tracklist is missing or has IDs, djlib listens to the set to find the songs, which takes a few minutes per hour of audio; `djlib identify LINK` does only that and says how sure it is about each song. Report owned, missing and other versions they own, and for a link how much of the set was named; IDs stay IDs. Offer next: rekordbox (`djlib set FILE`), missing songs (`--fetch`, after asking), the USB stick (`--usb`, while they're at the computer), or the songs they Shazamed (`djlib set shazam`). The dj-library skill has the details.

Report back: the djlib version, the music folders and how many tracks were indexed, anything `djlib doctor` flagged, and whether the plugin was added.
