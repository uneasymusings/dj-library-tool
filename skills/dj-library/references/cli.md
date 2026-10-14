# CLI recipes

Every command prints one JSON envelope (`schema_version`, `ok`, `result`, `warnings`, `error`) when its output is captured or piped, or with `--json`. In a terminal it prints a readable view instead. The workspace chosen with `djlib init` (or `djlib use PATH`) is the default, so `--workspace` is only needed for another one. Don't put the user's private paths into public examples or commits.

If a flag is rejected, the installed engine may be older than this skill: check `djlib version` and `djlib COMMAND --help`, and suggest updating ([install](install.md)).

## One command for a set

```bash
djlib --json set tracklist.txt --no-rekordbox                 # owned/missing and the crate; no rekordbox
djlib --json set tracklist.txt                                # … and the rekordbox playlist (macOS)
djlib --json set tracklist.txt --when-idle 60                 # … imported once the user has been away 60 s
djlib --json set 'https://soundcloud.com/USER/SET'            # a YouTube/SoundCloud set: its published tracklist, or what djlib hears
djlib --json set 'https://…' --tracklist 1001.txt             # … merged with a tracklist copied from 1001Tracklists
djlib --json set shazam:new                                   # the songs the user Shazamed since the last import
djlib --json set tracklist.txt --fetch --yes                  # also download clear matches for missing songs as MP3
djlib --json set tracklist.txt --usb                          # also export to the USB stick (the user clicks the playlist once)
djlib --json rekordbox push COLLECTION_ID [ID…] --when-idle 60  # existing crates → rekordbox playlists
djlib --json rekordbox usb COLLECTION_ID                      # an existing crate → USB, verified from the stick
```

A tracklist file has one `Artist - Title (Mix)` per line. Numbering, timestamps, `[Label]` suffixes and 1001Tracklists' copied `w/` and number-only lines are handled, and a heading line names the set (`--name` overrides it). `--source URL` keeps a set link with a file as evidence for unknown IDs.

With a set link, `set` listens to the set (as `djlib identify`, below) when its published tracklist is missing or has an ID, and reuses an earlier identification of it at no cost. `--identify` always listens, `--no-identify` never does, and `--tracklist FILE` (repeatable) adds a tracklist to merge with what djlib hears. With a file, `--identify` or `--tracklist` need `--source URL` and merge the file with what djlib hears. When song recognition isn't installed or set up, a link with a published tracklist uses that list, a warning says why and `listen_fix` holds the command that fixes it.

MixesDB is checked automatically: when the description and chapters list no tracklist, `set` uses MixesDB's for the set (a page counts only when it links the same upload, or names the same DJs and event with the same day or length). It is the published tracklist as above, also with `--no-identify` or without song recognition, except that `set` also listens when MixesDB doesn't call it complete. The view says “Tracklist from MixesDB: PAGE” with the page's link. 1001Tracklists still needs the browser (below).

The result has `owned`/`songs`, `missing[]` (`label`, `state`, `you_own` other versions, and when djlib listened `notes` on why the song is in the set and `confidence`, its status), `owned_to_check[]` (owned songs in the crate whose `confidence` isn't `confirmed`), `id_hints[]` (what listeners named near each ID's time: `label`, `mentions`, `evidence`), `identified` (null, or `coverage` with `percent` named and `percent_heard`, `counts`, `tracklist_file`, `job_id`, `reused`), `mixesdb` (null, or the page its tracklist came from: `page_title`, `page_url`, `tracklist` `complete`|`incomplete`|`unknown`, `match`), `fetched` (`chosen[].source`, `needs_your_pick[]` with `options`, `downloaded`, `failed`), `collection_id`, `playlist`, `rekordbox.status` (`imported`, `already_in_rekordbox` or `skipped` with a `reason`; `replaces` when a changed set became "Name (2)") and `usb` (`found`/`expected`, `in_order`, `analysis_from_device`, `player_playback_verified: false`). `set shazam…` adds `shazam` (`songs`, `songs_in_history`, `since`, `first_shazamed_at`, `newest_shazamed_at`, `sources`, `remaining`).

- Run `set`, `rekordbox push|usb|pull` in the background; `--usb` waits up to `--timeout` seconds (600) for the click and `--when-idle` waits for the user to step away.
- Ask before `--fetch --yes` (`--yes` only applies with `--fetch`). Only confident matches are downloaded; show `needs_your_pick` with the best guess and let the user choose, then `download --file` with that URL and the requested labels.
- Before `--usb`, tell the user: “In rekordbox, click the playlist NAME.” With `--json`, stderr carries `{"event": "select_playlist", "playlist": …, "device": …}` and, for a wrong click, `{"event": "wrong_playlist", "selected": …}`. Nothing else is exported. `--usb` can't be combined with `--when-idle` or `--no-rekordbox`; `--device /Volumes/NAME` picks the stick when several are mounted.
- Exit codes: 0 done, 2 input or application error (see `error.code`), 4 the USB check found missing or out-of-order files, 130 Ctrl-C (a set djlib was listening to stops; running it again continues).
- Without macOS, rekordbox or the Accessibility permission, `set` skips the rekordbox step (`rekordbox.status: skipped` with the reason) and still builds the crate.
- `SOURCE_BROWSER_ONLY`: a 1001Tracklists link. Read the page in the user's browser if you can, or ask them to copy the tracklist into a file, then pass it with `--tracklist FILE` and the set's YouTube or SoundCloud link; never try to get around its captcha.
- `TRACKLIST_NOT_FOUND`: the upload has no tracklist (common for Boiler Room), MixesDB has none for it, and djlib couldn't listen (or `--no-identify`); the message says why and lists what listeners named. `APP_PLAYLIST_NAME_TAKEN`: rekordbox has a different playlist with that name; rename one or pass `--name`.
- `--tracklist` with `--no-identify`, or `--identify`/`--tracklist` with `shazam`, is `INPUT_INVALID`.

## Every song in a set

```bash
djlib --json identify 'https://www.youtube.com/watch?v=VIDEO'               # read, listen, merge
djlib --json identify 'https://soundcloud.com/USER/SET' --tracklist 1001.txt  # … with a copied tracklist
djlib --json identify URL --again --recognizer audd                          # listen again, with AudD
```

`identify` reads the set's description, chapters and up to 1,000 comments, looks the set up on MixesDB, downloads its audio, listens to 12-second windows every 30 s (every minute when a tracklist times its songs), listens again where songs change, and merges everything into one tracklist (a few minutes per hour of audio). Options: `--tracklist FILE` (repeatable; remembered for the set, named `1001tracklists` or `mixesdb` when the file or text says so), `--again`, `--recognizer shazam|audd|acrcloud`, `--name`. Only one YouTube video or SoundCloud set per run; links are reduced to one form, so `youtu.be`, `&t=` and `&list=` don't matter.

While it runs, stderr carries `{"event": "identify_started", "job_id", "url"}`, then `{"event": "identify_progress", "job_id", "phase", "windows_done", "windows_total", "songs_heard", "eta_seconds"}` at each phase (`queued`, with `ahead` when another set goes first, `reading_page`, `downloading`, `listening`, `checking_gaps`, `merging`) and at most every 15 s, ending with phase `completed` or `failed`. While Shazam makes djlib wait, progress has `waiting` (`service`, `reason` `rate_limited` or `unreachable`, `retry_in`, `gives_up_in` seconds): tell the user it is slowing down, not stuck. `djlib jobs watch JOB_ID` shows the same progress. Ctrl-C cancels (exit 130, `JOB_CANCELLED` with `details.job_id`); what was heard and downloaded stays, so running it again continues.

The result has `job_id`, `source` (`url`, `provider`, `title`, `uploader`, `duration_seconds`), `recognizer` (`name`, `licensed`), `duration`, `coverage` (`percent` of the set with a named track, `seconds_identified`, `percent_heard` and `seconds_heard` where djlib heard the song placed there, `gaps[]` of 90 s or more where it heard none), `counts` (`tracks`, `confirmed`, `likely`, `weak`, `conflict`, `unidentified`), `tracks[]`, `unplaced[]` (songs heard in one window that fit no ID: not tracks), `listed` (whether any tracklist was published or given), `mixesdb` (the MixesDB page merged, as in `set`; its `sources[]` entry names it too), `tracklist_file` (text for `djlib set FILE`), `tracklist_json` (all evidence), `reused`, `notices`, `publisher_text` (a reminder that labels, evidence, candidates and skipped lines quote publishers and listeners) and `next`. `warnings` holds the notices, then tracklist lines djlib couldn't read.

Each track: `n`, `start` (`mm:ss`), `approx` (time guessed between neighbours), `kind` (`named` or `unknown`), `artist`, `title`, `version`, `status`, `evidence` (one line: who heard it in how many windows, where it is listed, and the other spelling when the listing and the audio name it differently: “listed in MixesDB as …” when djlib corrected a listing's misspelt credits from what it heard in 2+ windows, “Shazam named it …” otherwise), up to three `candidates` plus up to two more listener suggestions: `{source: "audio", label, heard_by, windows, at, strength}`, `{source: "comments", label, mentions, likes, at}`, or for a conflict the listing's song (`listed_in`); and for an ID found in a long stretch nobody heard, `songs_estimate` (about how many songs it holds at the set's pace). Statuses: `confirmed` the audio bears it out (heard where a listing names it, or heard in 3+ windows); `likely` one good source (listed but not heard, however many listings name it, since they copy each other; or heard in 2 windows; or listed and heard under another name, such as a remix heard as its sample, whose `evidence` says what each side has; for an ID, a song heard there in 2+ windows); `weak` only listeners' comments or a single window; `conflict` a listing names one song where djlib heard another; `unidentified` an ID nothing named. An ID keeps `artist: null`; its candidates are guesses.

MCP: `djlib_identify_set` (`url`, `idempotency_key`, optional `tracklist_text` with `tracklist_source` `1001tracklists`|`mixesdb`|`file`, `again`) returns the job; poll `djlib_job` every 10–20 s (`result.progress` while it runs, then the result above without `next`). After a failure, call again with a new key: it continues. To stop it, `djlib jobs control JOB_ID cancel` (MCP: `djlib_control` with `cancel`).

Errors:
- `DEPENDENCY_REQUIRED`: song recognition, FFmpeg or yt-dlp is missing; `djlib doctor` shows how to install it (song recognition: rerun the djlib installer).
- `RECOGNIZER_NOT_CONFIGURED`: AudD or ACRCloud keys are missing. Set them, run `djlib service stop` (the background service reads them when it starts), then retry. `RECOGNIZER_REJECTED`: the service refused the key or its quota is used up.
- `RECOGNITION_RATE_LIMITED`, `RECOGNITION_UNAVAILABLE`, `RECOGNITION_FAILING` (the service failed on 10 windows in a row or on more than a quarter of the set), `RECOGNIZER_REJECTED` (Shazam refused djlib, or a licensed service refused the key), `DISK_FULL`: what was heard is saved (`details.windows_heard`, `resumes: true`); fix the cause if there is one, then run the same command later and it continues, asking failed windows again. After `--again` fails, the earlier tracklist stays in use.
- `SET_PREVIEW_ONLY`: only a preview plays, as SoundCloud gives for Go+ uploads (`details.heard_seconds`, `set_seconds`). Copy the tracklist into a file and run `details.next` (`djlib set FILE --source URL`).
- `SOURCE_UNSUPPORTED`: not a YouTube or SoundCloud link. `SINGLE_SOURCE_REQUIRED`: a playlist or channel page. `SOURCE_BROWSER_ONLY`: a 1001Tracklists link; give the set's own link with `--tracklist FILE`.

The first identification with Shazam (the default recognizer) in a workspace adds a notice: djlib uses Shazam's service the way the Shazam app does, unofficially, so it may slow down or stop working. Licensed services need the user's account and keys in the background service's environment: `DJLIB_RECOGNIZER=audd` with `DJLIB_AUDD_TOKEN`, or `DJLIB_RECOGNIZER=acrcloud` with `DJLIB_ACRCLOUD_HOST`, `DJLIB_ACRCLOUD_KEY` and `DJLIB_ACRCLOUD_SECRET`. A set identified with one service is reused with a notice when another is asked for; `--again` listens with the new one. Each set's files live in the workspace's `sets/` folder; the audio is deleted after a successful listen, and audio a stopped or failed listen left behind a day after it was last used (`DJLIB_KEEP_SET_AUDIO=1` keeps it). Notices also say when the download came out short (the rest counts as unheard), when recognition failed on some windows, and when listener comments were skipped.

## Shazam history

```bash
djlib --json shazam status                      # where the history is, how many songs, what's new
djlib --json shazam list --since 2026-10-01     # songs, newest first (--from apple-music|PATH, --limit)
djlib --json set shazam                         # every Shazam as a set, in the order heard
djlib --json set shazam:new                     # only those since the last import
djlib --json set shazam:apple-music             # Apple Music's “My Shazam Tracks” (opens Music)
djlib --json set shazam:~/Backups/IPHONE_BACKUP # an unencrypted iPhone backup, ShazamDataModel.sqlite or a CSV
```

`set shazam` reads this Mac's Music Recognition history (which holds the iPhone's Shazams when Shazam syncs with iCloud) and the older Shazam app for Mac. A list holds 1,000 songs: from this Mac it takes the oldest and `shazam:new` continues; from a file or Apple Music it keeps the newest. `shazam:new` moves on only once every step of the set got through, and only for this Mac's history. Each song's notes say when and where it was Shazamed. MCP: `djlib_shazam_history` (`source`, `since`, `limit` 1–1000) lists them read-only.

- `SHAZAM_HISTORY_NOT_FOUND`: no history. `error.details.setup`: on the iPhone, Settings → name → iCloud → turn on Shazam; on the Mac, System Settings → Apple Account → iCloud → See All → turn on Music Recognition; open Control Center → Music Recognition once and wait a few minutes. `error.details.alternatives` lists `shazam:apple-music` and `shazam:BACKUP_OR_CSV`.
- `SHAZAM_ACCESS_DENIED`: macOS keeps the history private; allow the terminal app under Privacy & Security → Full Disk Access, quit and reopen it.
- `SHAZAM_NOTHING_NEW`: nothing since the last import (`details.last_import` names that list).
- `SHAZAM_BACKUP_ENCRYPTED`: use iCloud sync or an unencrypted Finder backup. `SHAZAM_HISTORY_UNREADABLE`: not a Shazam library djlib can read.
- `SHAZAM_MUSIC_NOT_ALLOWED`: allow the terminal app to control Music under Privacy & Security → Automation. `SHAZAM_PLAYLIST_NOT_FOUND`: Music has no “My Shazam Tracks”; connect Apple Music in the Shazam app. `SHAZAM_MUSIC_UNSUPPORTED`: Apple Music is read on a Mac only. `SHAZAM_MUSIC_FAILED`: Music didn't answer; try again.

```bash
djlib --json rekordbox sync                    # BPM and cues from rekordbox's analysis files, no window
djlib --json rekordbox pull --when-idle 60     # adds key through one XML export (uses rekordbox's window)
djlib --json source-inspect 'https://www.youtube.com/watch?v=VIDEO'
djlib --json status                            # library, request lists, crates in rekordbox, next steps
djlib schemas                                  # strict JSON inputs
```

MCP equivalents: `djlib_source_inspect` (`comments: 200` adds listener comments; `mixesdb` is MixesDB's tracklist when the page lists none), `djlib_identify_set`, `djlib_control`, `djlib_shazam_history`, `djlib_find_sources` (ranked YouTube/SoundCloud uploads for one song), `djlib_download`, `djlib_create_request`, `djlib_collect_request`, `djlib_import_rekordbox_analysis`. The rekordbox and USB steps are CLI-only.

## Request lists step by step

```bash
djlib --json requests create --text tracklist.txt     # or --file wanted.json
djlib --json requests get REQUEST_ID --after 0 --limit 100
djlib --json requests refresh REQUEST_ID --revision CURRENT
djlib --json requests collect REQUEST_ID              # crate of the owned songs, in list order
djlib --json requests report REQUEST_ID --revision CURRENT
```

Matching rules, JSON input and resolutions: [requests](requests.md).

## Library, saved work and jobs

```bash
djlib --json scan [FOLDER] --key music-scan-v1
djlib --json library --query "Joy Orbison" --limit 100 [--after NEXT_CURSOR]
djlib --json collections --query "Warm"
djlib --json collection COLLECTION_ID
djlib --json requests list --query "Friday"
djlib --json jobs get JOB_ID
djlib --json jobs wait JOB_ID --timeout 30
djlib --json jobs items JOB_ID --state failed
djlib --json jobs control JOB_ID pause|resume|cancel|retry
djlib --json reviews list --job-id JOB_ID
djlib --json reviews resolve REVIEW_ID --revision 1 --choice skip
```

`library --query` matches every word, ignoring case and accents. Pass `next_cursor` as `--after` with the same query until it is null. Review choices are `accept_requested`, `use_file_metadata` and `skip`. `jobs wait` exits 3 while the job is still running and 4 for attention, failure or a partial outcome. Accepted jobs survive the CLI or MCP process; cancelling keeps files already accepted.

Notes, BPM/key, filtered crates, folders and changed files: [organize](organize.md).

## Selected downloads

```json
{
  "name": "Friday web selections",
  "idempotency_key": "friday-web-v1",
  "tracks": [
    {"url": "https://soundcloud.com/ARTIST/RECORDING", "artist": "Artist", "title": "Track", "version": ""}
  ]
}
```

```bash
djlib --json download --file downloads.json
```

URLs are placeholders; `djlib_find_sources` (or `set --fetch`) finds and ranks real ones. Optional `alternates` (up to three URLs of the same recording) are tried in order when the first upload is gone. Public YouTube, SoundCloud and Bandcamp recordings only, up to 30 minutes each and 1,000 per request. Downloads are saved as MP3 at the source's quality, and the catalog copy is tagged with the supplied artist/title/version. They need the `download` extra and FFmpeg; YouTube also needs Deno or Node 22+.

## Collections from local files and exports

```json
{
  "name": "Friday warm-up",
  "profile": "club",
  "tracks": [
    {"path": "/Users/NAME/Music/track.flac", "artist": "Artist", "title": "Track", "version": "Extended Mix"}
  ]
}
```

```bash
djlib --json plan --file request.json
djlib --json start PLAN_ID --revision 1 --key friday-v1
djlib --json export COLLECTION_ID --key friday-export-v1
djlib --json usb-preflight /Volumes/DJ_USB --required-bytes 1000000000
```

`club` indexes in place; `archive` copies accepted audio into the workspace. `export` writes a hash-checked manifest, an M3U8 and an experimental rekordbox XML; for rekordbox itself prefer `rekordbox push`. `usb-preflight` only reads free space. Working-copy deliveries for rekordbox or Serato: [delivery](delivery.md).

## Service and startup errors

Commands start the background service on demand, and it stops after 30 idle minutes; `djlib service start` starts one that keeps running. `service status` inspects it and `service stop` stops it; accepted work stays saved and resumes on the next start.

- `SERVICE_START_BUSY`: another starter held the lock for 45 seconds. Check `service status` before retrying.
- `SERVICE_START_FAILED`: the service exited. Read the workspace's `runtime/service.log`, fix the cause, then retry.
- `SERVICE_START_TIMEOUT`: no healthy service within 30 seconds while the process still ran. Check status and the log before retrying; don't launch repeatedly.
- `COORDINATOR_VERSION_MISMATCH`: an older service is still busy. When its jobs finish, `service stop`, then retry.
- `CLIENT_OUTDATED`: djlib was updated under this session. Ask the user to reconnect djlib (Claude Code: `/mcp` → djlib → Reconnect) or restart the assistant; don't stop the service or retry.
- `COORDINATOR_START_REQUIRED` (Windows MCP): run `djlib service start` in an ordinary terminal, then reconnect.

None of these submits a music job. A later uncertain submission still uses its original idempotency key. Redact logs before sharing.
