# Troubleshooting

Every command reports errors as `error.code` and a message in its JSON envelope (or a readable panel in a terminal). The common ones, by area:

## Installation

- **`djlib: command not found`.** `uv tool dir --bin` shows where uv put it; `uv tool update-shell` adds that folder to PATH. Open a new terminal afterwards. See [uv's tool guide](https://docs.astral.sh/uv/guides/tools/#installing-tools).
- **Use a persistent install for assistants.** An MCP registration or generated session stores the path of the installed `djlib`. An ephemeral `uvx` environment or a deleted developer checkout breaks it; reinstall with `uv tool install` and create a new session.
- **`djlib doctor`** checks FFmpeg/ffprobe, yt-dlp, Deno or Node, song recognition, rekordbox, the Accessibility permission and the background service without starting it, shows your Mac's Shazam history, and flags a Claude Code plugin from another release.
- **Release files** come with `SHA256SUMS` to verify downloads. The source checkout's `uv.lock` is the exact dependency record for contributors and CI.

## Workspace

- **`WORKSPACE_REQUIRED`**: no workspace yet. Run `djlib init --allow-root ~/Music` with your music folder. The MCP server keeps running and works as soon as the workspace exists.
- **`ROOTS_NOT_UPDATED`**: `init --allow-root` on an existing workspace doesn't add folders. Use `djlib roots add PATH`; existing folders stay and no music moves.
- **Upgrades** back up the catalog database under the workspace's `backups/` before migrating. Music and rekordbox's own data are not part of that backup, and there is no automatic restore.

## Background service

Commands start a background service for the workspace on demand. Started that way, it stops after 30 idle minutes; `djlib service start` starts one that keeps running. `djlib service status` shows it and `djlib service stop` stops it. Accepted jobs stay saved and resume on the next start.

| Error | What to do |
| --- | --- |
| `SERVICE_START_BUSY` | Another command held the startup lock for 45 seconds. Check `djlib service status`, then retry. |
| `SERVICE_START_FAILED` | The service exited. Read the workspace's `runtime/service.log`, fix the cause it names, then retry. |
| `SERVICE_START_TIMEOUT` | No healthy service within 30 seconds while the process still ran. Check `service status` and the log before retrying; don't start it repeatedly. |
| `COORDINATOR_VERSION_MISMATCH` | An older service is still busy after an update (an idle one is replaced by itself). When its jobs finish, `djlib service stop`, then retry. |
| `CLIENT_OUTDATED` | djlib was updated while this session kept running the old one, often an assistant's MCP server. Restart that session (in Claude Code: `/mcp` → djlib → Reconnect; in Codex: restart Codex). The newer service is left alone; retrying doesn't help. |

A startup error never means a music job was accepted. Redact private paths from logs before sharing them.

## Windows and MCP

On Windows, an MCP server never starts the background service itself: assistant hosts end every process the server starts when they disconnect, which would take the service with them. Start it from an ordinary terminal first:

```powershell
djlib service start
```

Then open or reconnect Claude Code or Codex. `COORDINATOR_START_REQUIRED` means this step is missing; retrying the tool doesn't help. Sessions made with `djlib setup-agent` do this for you: `python launch.py claude` starts the service before the host. Several hosts can share one service and catalog.

## rekordbox and USB (macOS)

| Error | What to do |
| --- | --- |
| `APP_AUTOMATION_NOT_ALLOWED` | Allow your terminal app under System Settings → Privacy & Security → Accessibility, then retry. |
| `APP_SCREEN_LOCKED` | The Mac is locked, and no app can be driven then. Retry once it's unlocked (USB steps wait for the unlock themselves). |
| `APP_PLAYLIST_NAME_TAKEN` | rekordbox has a different playlist with that name. Rename or delete it, or pass `--name`. Nothing was imported. |
| `APP_SELECTION_TIMEOUT` | The playlist wasn't selected in time; nothing was exported. The message says why (rekordbox wasn't in front, no playlist was selected, the stick wasn't listed under Playlist > Export Playlist, or another playlist was selected). Run the USB step again and click the playlist named in the prompt and the notification. |
| `DEVICE_REQUIRED` | No stick or several sticks are mounted. Pass `--device /Volumes/NAME`. |
| `APP_AUTOMATION_UNSUPPORTED` | Not a Mac. `set` still builds the crate; import it into your DJ app yourself. |
| Exit code 4 | The USB check found missing or out-of-order files. Don't play from that stick; export again. |

djlib never edits rekordbox's database and never writes the USB stick itself; rekordbox does the export. Playback on CDJ/XDJ players is not verified by the tool, so test the stick before a gig.

## Listening to sets and Shazam history

| Error | What to do |
| --- | --- |
| `DEPENDENCY_REQUIRED` | Song recognition, FFmpeg or yt-dlp is missing. Rerun the one-line installer (it installs djlib with song recognition on Python 3.12); `djlib doctor` shows what is missing. |
| `RECOGNIZER_NOT_CONFIGURED` | AudD or ACRCloud is chosen without its keys. Set them (see [song recognition](INSTALL.md#song-recognition)), run `djlib service stop`, then try again. |
| `RECOGNIZER_REJECTED` | The service refused your key, or its quota is used up. Check the key in your account. |
| `RECOGNITION_RATE_LIMITED`, `RECOGNITION_UNAVAILABLE` | The recognition service is turning requests away or can't be reached. What djlib heard so far is saved: run the same command later and it continues. |
| `SET_PREVIEW_ONLY` | Only a preview of the set plays (SoundCloud gives 30 seconds of Go+ uploads). Copy its tracklist into a file and run `djlib set FILE --source LINK`. |
| `SINGLE_SOURCE_REQUIRED`, `SOURCE_UNSUPPORTED` | Use the link of one YouTube video or SoundCloud set, not a playlist, channel or another site. |
| `TRACKLIST_NOT_FOUND` | The upload has no tracklist and djlib couldn't listen; the message says why and what listeners named. |
| `SHAZAM_HISTORY_NOT_FOUND` | No Shazam history on this Mac. On your iPhone, Settings → your name → iCloud → turn on Shazam; on the Mac, System Settings → Apple Account → iCloud → See All → turn on Music Recognition; open Control Center → Music Recognition once and wait a few minutes. Or read an iPhone backup or CSV with `djlib set shazam:PATH`. |
| `SHAZAM_ACCESS_DENIED` | macOS keeps the history private. Allow your terminal app under System Settings → Privacy & Security → Full Disk Access, quit and reopen it. |
| `SHAZAM_NOTHING_NEW` | Nothing was Shazamed since your last `djlib set shazam:new`. |

The first identification that uses Shazam says once that djlib uses Shazam's service unofficially, so it may slow down or stop working; AudD and ACRCloud are the licensed options.

## Downloads

`SOURCE_BROWSER_ONLY` means a 1001Tracklists link: copy its tracklist into a text file. `SOURCE_AUTH_REQUIRED`, `SOURCE_RATE_LIMITED` and `SOURCE_UNAVAILABLE` come from YouTube or SoundCloud; try another upload or later. Cookie-based access is not supported.
