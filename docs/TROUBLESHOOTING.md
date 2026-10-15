# Troubleshooting

In a terminal, djlib says what went wrong in plain words and gives you the next command to run. Assistants and scripts get the same problem as a code in djlib's JSON reply; the codes are in brackets below so you can search for them.

## Install and upgrade

- **`djlib: command not found`.** Open a new terminal window. Still missing? `uv tool update-shell` adds djlib's folder to your PATH (`uv tool dir --bin` shows where it is). See [uv's tool guide](https://docs.astral.sh/uv/guides/tools/#installing-tools).
- **A Python error (`ModuleNotFoundError`) at the end of `djlib upgrade` from 0.1.0a15.** 0.1.0a15 runs its own upgrade code one last time, after a quiet wait. The upgrade worked: run `djlib doctor` to confirm.
- **Song recognition isn't installed after an upgrade.** `djlib doctor` says so; run `djlib upgrade` once more and it adds it.
- **`djlib doctor`** checks FFmpeg, yt-dlp, the YouTube helper (Deno or Node), song recognition, rekordbox, the Accessibility permission and the background service without starting it. It also shows your Mac's Shazam history and djlib's folder, and flags a Claude Code plugin from another release.
- **Use a lasting install for assistants.** An MCP registration or `setup-agent` session stores the path of the installed `djlib`. A temporary `uvx` environment or a deleted checkout breaks it; reinstall with `uv tool install` and make a new session.
- **Release files** come with `SHA256SUMS` to check your downloads.

## Tracklists and links

| What you see | What to do |
| --- | --- |
| `zsh: no matches found: https://…` | Put the link in single quotes: `djlib set 'https://…'`. Or copy the link and run `djlib set paste`. |
| **That link needs its https://** | Give the whole link, in single quotes. |
| **Can't read that tracklist** | TextEdit's rich text: on a Mac djlib converts it; elsewhere choose Format → Make Plain Text and save, or copy the tracklist and run `djlib set paste`. UTF-8, UTF-16 and Windows text files read as they are. A Word file or other document needs saving as plain text. An empty file needs the tracklist pasted into it. |
| **Copy the tracklist too** | The clipboard still holds the set's link. Copy the tracklist on the page, then run the same command again. With `--tracklist paste`, type the command with the link first, copy the tracklist last, then press Enter. |
| **Copy just the set's link** | You copied more than one link. Copy only the set's link, then run it again. |
| **No set or tracklist in the clipboard** | Copy the set's link or its tracklist. From a spreadsheet? Copy its Artist and Title header row too. |
| **Open this one in your browser** (`SOURCE_BROWSER_ONLY`) | A 1001Tracklists link (1001.tl short links too). djlib never reads 1001Tracklists. On a Mac, copy the tracklist and run `djlib set paste`. Better: `djlib set 'LINK' --tracklist paste` with the set's own YouTube or SoundCloud link (under the player), so djlib also listens. On Linux and Windows, save the tracklist as a text file and use `--tracklist FILE`. An assistant that can browse can copy it for you. |
| **That link isn't supported** (`SOURCE_UNSUPPORTED`) | Mixcloud doesn't allow downloads, so djlib can't listen to it: use the same set on YouTube or SoundCloud, or its copied tracklist. For other sites, use the YouTube or SoundCloud link of one set. |
| **Use the link of one set** (`SINGLE_SOURCE_REQUIRED`) | Use one video or one SoundCloud set, not a playlist or channel. |
| **Only a preview of that set plays** (`SET_PREVIEW_ONLY`) | SoundCloud plays only 30 seconds of Go+ uploads. Copy the tracklist and run `djlib set paste --source 'LINK'`. |
| **No tracklist on that page** (`TRACKLIST_NOT_FOUND`) | djlib couldn't name any song and nothing lists them. Copy a tracklist (for example from 1001Tracklists) and run `djlib set 'LINK' --tracklist paste`. |

On Windows, a link in single quotes works as typed in cmd.exe too.

## Shazam history

**No Shazam history found** (`SHAZAM_HISTORY_NOT_FOUND`). djlib finds no Shazams on this Mac. `djlib shazam status` and `djlib set shazam` print these steps too:

1. On your iPhone, open Shazam → Settings → turn on iCloud Sync. (If it's off or greyed out: Settings → [your name] → iCloud → See All → turn on Shazam and Music Recognition.)
2. On this Mac: System Settings → [your name] → iCloud → See All → turn on Music Recognition.
3. Add Recognize Music to the menu bar and click it once (macOS 26: Control Center → Edit Controls → Recognize Music; macOS 14–15: System Settings → Control Center → Music Recognition → Show in Menu Bar). Your iPhone's Shazams arrive within a few minutes.

Then Shazam one song and run `djlib shazam status` again. Other ways in: `djlib set shazam:apple-music` (if Shazam saves to Apple Music's “My Shazam Tracks”), or `djlib set shazam:PATH` with an iPhone backup folder.

| What you see | What to do |
| --- | --- |
| **djlib can't open your Shazam history yet** (`SHAZAM_ACCESS_DENIED`) | Allow the app you run djlib in (Terminal, iTerm, VS Code or Claude) under System Settings → Privacy & Security → Full Disk Access, quit and reopen it. `djlib shazam status` says so too, and its Next step opens the setting. |
| **Nothing new since …** (`SHAZAM_NOTHING_NEW`) | Not a problem: you haven't Shazamed anything since your last Shazam set. Shazam some songs, wait a few minutes for them to sync, then run it again. djlib points you to the crate with your newest Shazams. |
| **That iPhone backup is encrypted** (`SHAZAM_BACKUP_ENCRYPTED`) | Turn on Shazam in iCloud instead (above), or make an unencrypted backup in Finder. |
| **Music has no “My Shazam Tracks” playlist** (`SHAZAM_PLAYLIST_NOT_FOUND`) | In the Shazam app on your iPhone, open Settings and connect Apple Music, then wait for the playlist to appear in Music on this Mac. |
| Your shazam.com list stops updating | shazam.com's list stops updating once Shazam syncs with iCloud; then use your Mac's history (`djlib shazam status`). The CSV comes from shazam.com/myshazam → Download CSV, if your account shows it: `djlib set ~/Downloads/shazamlibrary.csv`. |

## Listening to sets

An hour-long set usually takes 5–10 minutes; when Shazam is busy it can take half an hour. While Shazam is slow, the progress line says “Shazam is slow, waiting (Ctrl-C is safe)”. Ctrl-C keeps what djlib heard; run the same command later to carry on.

| What you see | What to do |
| --- | --- |
| **Something needs installing** (`DEPENDENCY_REQUIRED`) | Song recognition, FFmpeg or the YouTube helper is missing. Rerun the one-line installer; `djlib doctor` shows what's missing. |
| **Song recognition is turning requests away** (`RECOGNITION_RATE_LIMITED`), **can't be reached** (`RECOGNITION_UNAVAILABLE`) or keeps failing (`RECOGNITION_FAILING`) | What djlib heard so far is saved. Run the same command later and it carries on, asking again for the parts it missed. |
| **The recognition service refused djlib's key** (`RECOGNIZER_REJECTED`) | Shazam refused djlib's requests (try again later, or `djlib upgrade`), or a licensed service refused your key or its quota is used up. What djlib heard is saved. |
| **Song recognition isn't set up** (`RECOGNIZER_NOT_CONFIGURED`) | AudD or ACRCloud is chosen without its keys. Set them (see [song recognition](INSTALL.md#song-recognition)), run `djlib service stop`, then try again. |
| `DISK_FULL` | The disk with djlib's folder is full; the message says how much a set needs. Free some space and run the same command again. |
| **The site didn't answer just now** (`SOURCE_FAILED`) while downloading the set | djlib already tried twice. Wait a minute and run the same command again; what arrived is kept. What the site said is in `runtime/downloads.log` in djlib's folder (`djlib doctor` shows where it is). |

The first listen that uses Shazam says once that djlib uses Shazam's service unofficially, so it may slow down or stop working. AudD and ACRCloud are the licensed options.

## rekordbox and USB (Mac)

| What you see | What to do |
| --- | --- |
| **macOS hasn't allowed djlib to control apps yet** (`APP_AUTOMATION_NOT_ALLOWED`) | Allow the app you run djlib in under System Settings → Privacy & Security → Accessibility, then try again. |
| **Your Mac is locked** (`APP_SCREEN_LOCKED`) | No app can be driven while the Mac is locked. Try again once it's unlocked (USB steps wait for the unlock themselves). |
| **rekordbox already has a different playlist with that name** (`APP_PLAYLIST_NAME_TAKEN`) | Rename or delete it in rekordbox, or pass `--name`. Nothing was imported. |
| **You didn't pick the playlist in time** (`APP_SELECTION_TIMEOUT`) | Nothing was exported. The message says why: rekordbox wasn't in front, no playlist was selected, the stick wasn't listed under Playlist > Export Playlist, or another playlist was selected. Run the USB step again and click the playlist it names. |
| **Which USB?** (`DEVICE_REQUIRED`) | No stick, or several sticks, are plugged in. Pass `--device /Volumes/NAME`. |
| **Driving rekordbox needs a Mac** (`APP_AUTOMATION_UNSUPPORTED`) | `djlib set` still builds the crate; import it into your DJ app yourself. |
| **USB check failed … don't take this stick yet** (exit code 4) | Tracks are missing or out of order on the stick. Don't play from it; export again. |
| A set you checked with 0.1.0a15 shows up as “Name (2)” in rekordbox | 0.1.0a16 found more songs you own, or now reads the untimed ID lines in your tracklist file, so it made a new crate. Delete the older playlist. |

djlib never edits rekordbox's database and never writes the USB stick itself; rekordbox does the export. Playback on CDJ and XDJ players isn't verified by the tool, so test the stick before a gig.

## Downloads

**That page needs a login** (`SOURCE_AUTH_REQUIRED`), **The site is limiting requests** (`SOURCE_RATE_LIMITED`) and **That recording isn't available** (`SOURCE_UNAVAILABLE`) come from YouTube or SoundCloud: try another upload, or later. djlib doesn't use browser cookies.

## Background service

djlib starts a background service on demand. Started that way, it stops after 30 idle minutes; `djlib service start` starts one that keeps running. `djlib service status` shows it and `djlib service stop` stops it. Accepted jobs stay saved and carry on at the next start. A copy of your djlib folder (a backup or a test copy) runs its own service and never writes into the original; the same folder typed with different capitals on a Mac uses the same service.

| What you see | What to do |
| --- | --- |
| **djlib isn't set up yet** (`WORKSPACE_REQUIRED`) | Run `djlib init ~/Music` with your music folder, then `djlib scan`. An assistant's djlib tools work as soon as it's set up. |
| **djlib's background service is still starting** (`SERVICE_START_BUSY`) | Another command is starting it. Check `djlib service status`, then try again. |
| **djlib's background service didn't start** (`SERVICE_START_FAILED`, `SERVICE_START_TIMEOUT`) | Read `runtime/service.log` in djlib's folder (`djlib doctor` shows where it is) and fix what it names. Check `djlib service status` before trying again; don't start it over and over. |
| **djlib was updated** (`COORDINATOR_VERSION_MISMATCH`) | An older service is still busy after an update (an idle one is replaced by itself). When its jobs finish, run `djlib service stop`, then try again. |
| **A newer djlib is running** (`CLIENT_OUTDATED`) | djlib was updated while this session kept the old one, often an assistant's. Restart that session (Claude Code: `/mcp` → djlib → Reconnect; Codex: restart Codex). Trying again doesn't help. |

A startup error never means a music job was accepted. Remove private paths from logs before sharing them.

## Windows and MCP

On Windows, an assistant's MCP server never starts the background service itself: assistant apps end every process the server starts when they disconnect, which would take the service with them. Start it from an ordinary terminal first:

```powershell
djlib service start
```

Then open or reconnect Claude Code or Codex. **djlib's background service isn't running** (`COORDINATOR_START_REQUIRED`) means this step is missing; trying the tool again doesn't help. Sessions made with `djlib setup-agent` do this for you: `python launch.py claude` starts the service before the app. Several assistants can share one service.

## Backups

Upgrades back up djlib's index of your music (its sets and crates too) under djlib's folder in `backups/` before changing it. Your music and rekordbox's own data aren't part of that backup, and there is no automatic restore.
