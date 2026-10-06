# CLI recipes

Every command prints one JSON envelope (`schema_version`, `ok`, `result`, `warnings`, `error`) when its output is captured or piped, or with `--json`. In a terminal it prints a readable view instead. The workspace chosen with `djlib init` (or `djlib use PATH`) is the default, so `--workspace` is only needed for another one. Don't put the user's private paths into public examples or commits.

If a flag is rejected, the installed engine may be older than this skill: check `djlib version` and `djlib COMMAND --help`, and suggest updating ([install](install.md)).

## One command for a set

```bash
djlib --json set tracklist.txt --no-rekordbox                 # owned/missing and the crate; no rekordbox
djlib --json set tracklist.txt                                # … and the rekordbox playlist (macOS)
djlib --json set tracklist.txt --when-idle 60                 # … imported once the user has been away 60 s
djlib --json set 'https://soundcloud.com/USER/SET'            # tracklist from a YouTube/SoundCloud set's description or chapters
djlib --json set tracklist.txt --fetch --yes                  # also download clear matches for missing songs as MP3
djlib --json set tracklist.txt --usb                          # also export to the USB stick (the user clicks the playlist once)
djlib --json rekordbox push COLLECTION_ID [ID…] --when-idle 60  # existing crates → rekordbox playlists
djlib --json rekordbox usb COLLECTION_ID                      # an existing crate → USB, verified from the stick
```

A tracklist file has one `Artist - Title (Mix)` per line. Numbering, timestamps, `[Label]` suffixes and 1001Tracklists' copied `w/` and number-only lines are handled, and a heading line names the set (`--name` overrides it). `--source URL` keeps a set link with a file as evidence for unknown IDs.

The result has `owned`/`songs`, `missing[]` (`label`, `state`, `you_own` other versions), `id_hints[]` (what listeners named near each ID's time: `label`, `mentions`, `evidence`), `fetched` (`chosen[].source`, `needs_your_pick[]` with `options`, `downloaded`, `failed`), `collection_id`, `playlist`, `rekordbox.status` (`imported`, `already_in_rekordbox` or `skipped` with a `reason`; `replaces` when a changed set became "Name (2)") and `usb` (`found`/`expected`, `in_order`, `analysis_from_device`, `player_playback_verified: false`).

- Run `set`, `rekordbox push|usb|pull` in the background; `--usb` waits up to `--timeout` seconds (600) for the click and `--when-idle` waits for the user to step away.
- Ask before `--fetch --yes` (`--yes` only applies with `--fetch`). Only confident matches are downloaded; show `needs_your_pick` with the best guess and let the user choose, then `download --file` with that URL and the requested labels.
- Before `--usb`, tell the user: “In rekordbox, click the playlist NAME.” With `--json`, stderr carries `{"event": "select_playlist", "playlist": …, "device": …}` and, for a wrong click, `{"event": "wrong_playlist", "selected": …}`. Nothing else is exported. `--usb` can't be combined with `--when-idle` or `--no-rekordbox`; `--device /Volumes/NAME` picks the stick when several are mounted.
- Exit codes: 0 done, 2 input or application error (see `error.code`), 4 the USB check found missing or out-of-order files.
- Without macOS, rekordbox or the Accessibility permission, `set` skips the rekordbox step (`rekordbox.status: skipped` with the reason) and still builds the crate.
- `SOURCE_BROWSER_ONLY`: a 1001Tracklists link. Read the page in the user's browser if you can, or ask them to copy the tracklist into a file; never try to get around its captcha.
- `TRACKLIST_NOT_FOUND`: the upload has no tracklist (common for Boiler Room); the message lists what listeners named. `APP_PLAYLIST_NAME_TAKEN`: rekordbox has a different playlist with that name; rename one or pass `--name`.

```bash
djlib --json rekordbox sync                    # BPM and cues from rekordbox's analysis files, no window
djlib --json rekordbox pull --when-idle 60     # adds key through one XML export (uses rekordbox's window)
djlib --json source-inspect 'https://www.youtube.com/watch?v=VIDEO'
djlib --json status                            # library, request lists, crates in rekordbox, next steps
djlib schemas                                  # strict JSON inputs
```

MCP equivalents: `djlib_source_inspect` (`comments: 200` adds listener comments), `djlib_find_sources` (ranked YouTube/SoundCloud uploads for one song), `djlib_download`, `djlib_create_request`, `djlib_collect_request`, `djlib_import_rekordbox_analysis`. The rekordbox and USB steps are CLI-only.

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

URLs are placeholders; `djlib_find_sources` (or `set --fetch`) finds and ranks real ones. Public YouTube, SoundCloud and Bandcamp recordings only, up to 30 minutes each and 1,000 per request. Downloads are saved as MP3 at the source's quality, and the catalog copy is tagged with the supplied artist/title/version. They need the `download` extra and FFmpeg; YouTube also needs Deno or Node 22+.

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
- `COORDINATOR_VERSION_MISMATCH`: an older service is running. `service stop`, then retry.
- `COORDINATOR_START_REQUIRED` (Windows MCP): run `djlib service start` in an ordinary terminal, then reconnect.

None of these submits a music job. A later uncertain submission still uses its original idempotency key. Redact logs before sharing.
