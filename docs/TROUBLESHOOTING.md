# Troubleshooting

Every command reports errors as `error.code` and a message in its JSON envelope (or a readable panel in a terminal). The common ones, by area:

## Installation

- **`djlib: command not found`.** `uv tool dir --bin` shows where uv put it; `uv tool update-shell` adds that folder to PATH. Open a new terminal afterwards. See [uv's tool guide](https://docs.astral.sh/uv/guides/tools/#installing-tools).
- **Use a persistent install for assistants.** An MCP registration or generated session stores the path of the installed `djlib`. An ephemeral `uvx` environment or a deleted developer checkout breaks it; reinstall with `uv tool install` and create a new session.
- **`djlib doctor`** checks FFmpeg/ffprobe, yt-dlp, Deno or Node, rekordbox, the Accessibility permission and the background service without starting it.
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
| `COORDINATOR_VERSION_MISMATCH` | An older service is still running after an update. `djlib service stop`, then retry. |

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

## Downloads

`SOURCE_BROWSER_ONLY` means a 1001Tracklists link: copy its tracklist into a text file. `SOURCE_AUTH_REQUIRED`, `SOURCE_RATE_LIMITED` and `SOURCE_UNAVAILABLE` come from YouTube or SoundCloud; try another upload or later. Cookie-based access is not supported.
