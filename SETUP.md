# Set up djlib with your AI assistant

Paste this into Claude Code or Codex:

> Set up djlib for me by following https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/SETUP.md

The rest of this page is for the assistant. It takes a few minutes, most of it indexing music.

## Steps for the assistant

1. **Install or update.** Run `djlib --version`. If it's missing, or `djlib upgrade --check` reports `update_available: true`, install with (macOS or Linux; it installs software, so ask first):

   ```bash
   curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh
   ```

   It installs uv if needed, the newest djlib (with Shazam listening and downloads), FFmpeg through Homebrew, and the djlib plugin for Claude Code and Codex when they're installed. Running it again updates everything. On Windows, follow [installation](docs/INSTALL.md) instead. If `djlib` isn't found afterwards, use `~/.local/bin/djlib`.

2. **Index their music.** Ask where their music is (usually `~/Music`; several folders and external drives are fine). Run `djlib init FOLDER` with each folder, quoted (`djlib init ~/Music '/Volumes/DJ Drive/Music'`), then `djlib scan` in the background. Scanning reads files in place and never moves, renames or retags them; a first scan takes about 4 minutes per 1,000 tracks. `djlib status` shows the count.

3. **rekordbox on a Mac.** Run `djlib doctor`. If `rekordbox_automation_allowed` is false, ask them to turn on the app that runs you (Terminal, iTerm, VS Code or Claude) under System Settings → Privacy & Security → Accessibility, then restart that app. Making rekordbox playlists and USB sticks needs it; checking sets doesn't.

4. **Shazam (Mac, optional).** Run `djlib shazam status`. If it finds songs, tell them how many and the newest, and offer the last 30 days: `djlib set SPEC --no-rekordbox`, where SPEC is `result.recent_spec`. (With more than 100 Shazams the status's Next step prints the same `shazam:DATE` command; with fewer it offers `djlib set shazam`, the whole short history, which is fine too.) For a long history, don't offer `djlib set shazam` or `shazam:new` first: with no earlier Shazam set, both mean the whole history, oldest first, 1,000 at a time. If it finds nothing, read them these three steps word for word, then check again in a few minutes:

   1. On your iPhone, open Shazam → Settings → turn on iCloud Sync. (If it's off or greyed out: Settings → [your name] → iCloud → See All → turn on Shazam and Music Recognition.)
   2. On this Mac: System Settings → [your name] → iCloud → See All → turn on Music Recognition.
   3. Add Recognize Music to the menu bar and click it once (macOS 26: Control Center → Edit Controls → Recognize Music; macOS 14–15: System Settings → Control Center → Music Recognition → Show in Menu Bar). Your iPhone's Shazams arrive within a few minutes.

   If it says djlib can't open the history, ask them to allow the app they run you in under System Settings → Privacy & Security → Full Disk Access, then quit and reopen it. No iPhone sync? `djlib set shazam:apple-music --no-rekordbox` reads Apple Music's “My Shazam Tracks”, and `djlib set ~/Downloads/shazamlibrary.csv --no-rekordbox` a CSV from shazam.com/myshazam.

5. **Load the plugin.** The installer added it; it takes effect after a restart of Claude Code or Codex (in Claude Code, `/mcp` → djlib → Reconnect also works). Until then, use the `djlib` CLI directly; it does everything.

6. **First set.** Ask for a YouTube or SoundCloud link to a set, or a tracklist.

   - **A link:** run `djlib set 'LINK' --no-rekordbox` in the background, with the link in single quotes. When the tracklist is missing, has IDs or names too few songs for the set, djlib listens to it. An hour-long set usually takes 5–10 minutes; when Shazam is busy it can take half an hour. Ctrl-C stops it; run the same command later to carry on. Tell them so, end your turn, and report when it finishes. A 1001Tracklists link can't be read: ask for the set's own YouTube or SoundCloud link and the tracklist they copied, then pipe the tracklist in with `--tracklist -`. With the link djlib listens to check and time the list (5–10 minutes an hour; at once if it heard the set before); without it, `djlib set -` is at once but has no links into the set. Ask which they want when the set is new.
   - **A tracklist they gave you:** pipe it in, `printf '%s' "$TRACKLIST" | djlib set - --name 'Set name' --no-rekordbox`, or write it to a file (one `Artist - Title (Mix)` per line) and run `djlib set FILE --no-rekordbox`. Never run `djlib set paste` or `--tracklist paste` yourself: they read the user's clipboard, not what they gave you.

   Report owned and missing songs (with how sure djlib is), songs that may be theirs under another artist name (`own: maybe`; they check them before buying), other mixes they own, and IDs as “ID, maybe …”; IDs stay IDs. For a link, say how many tracks Shazam heard. Offer next: their rekordbox playlist (the same command without `--no-rekordbox`), their shopping list (`djlib requests report <result.request_id>`), downloads (`--fetch`, after asking), or their USB stick (`--usb`, while they're at the computer). The dj-library skill has the details.

Report back: the djlib version, the music folders and how many tracks were indexed, how many Shazams djlib found, anything `djlib doctor` flagged, and whether the plugin was added.
