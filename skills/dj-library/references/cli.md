# CLI reference

When its output is captured or piped (as in an assistant's shell), or with `--json`, every command prints one JSON envelope: `schema_version`, `ok`, `result`, `warnings`, `error`. Captured, it is one line; `--json` at a terminal indents it. In a terminal it prints a readable view instead. The workspace chosen with `djlib init` (or `djlib use PATH`) is the default; `--workspace PATH` picks another. `--json` and `--workspace` work before or after the command, though `djlib --help` doesn't list them. Don't put the user's private paths into public examples or commits.

Errors keep their `error.code` in JSON; the terminal shows a plain title and the steps instead. `error.details` may carry `title`, `next` with `next_label` (the first step) and `next_steps` (`[{label, command, note?}]`, every way forward). Run a suggested command only when it fits; **never run one with `paste`**: it reads the user's clipboard. Pipe the text into `-` instead.

If a flag is rejected, the installed engine may be older than this skill: check `djlib --version` and `djlib COMMAND --help`, and suggest updating ([install](install.md)).

## A set: `djlib set`

```bash
djlib set 'https://soundcloud.com/USER/SET' --no-rekordbox     # a set link: every track, owned or missing, and the crate
djlib set 'https://…' --tracklist - --no-rekordbox < 1001.txt  # … with a tracklist copied from 1001Tracklists, piped in
djlib set tracklist.txt --no-rekordbox                         # a text file, one "Artist - Title (Mix)" per line
djlib set - --no-rekordbox < tracklist.txt                     # tracklist text piped in (assistants: a quoted heredoc)
djlib set 'Warehouse Friday.csv' --no-rekordbox                # a spreadsheet with Artist and Title (and Mix) columns
djlib set shazam:2026-09-14 --no-rekordbox                     # the songs the user Shazamed since that date
djlib set tracklist.txt                                        # … and a rekordbox playlist (macOS)
djlib set tracklist.txt --when-idle 60                         # … imported once the user has been away 60 s
djlib set tracklist.txt --usb                                  # … and onto the USB stick (the user clicks the playlist once)
djlib set tracklist.txt --fetch --yes                          # also download clear matches for missing songs (ask first)
djlib rekordbox push CRATE [CRATE…] --when-idle 60             # crates already made → rekordbox playlists
djlib rekordbox usb CRATE                                      # a crate → USB, checked on the stick
```

`djlib set paste` (and `--tracklist paste`) reads what the user copied, on a Mac. It's for people typing in a terminal; an assistant pipes the text into `-`. Put links in single quotes, or zsh stops at the `?`. CRATE is a crate name, the start of its ID (6+ characters) or `last`.

**Tracklist text.** One `Artist - Title (Mix)` per line. Numbering, timestamps (every line keeps its own), `[Label]` and 1001Tracklists' copied `w/` and number-only lines are handled; a time on a line of its own is the next track's. `ID - ID` and `Artist - ID` lines stay IDs in their place, timed or not. A heading line names the set (`--name` overrides it); an untitled paste is named "Pasted 13 Oct · first song + N more". TextEdit rich text is converted on a Mac, and UTF-8, UTF-16 and Windows-1252 files read as they are. Over 3 skipped label or heading lines show as one line in the terminal; `warnings` still lists each, and a tracklist given before for the same set doesn't repeat them. `--source URL` keeps the set's link with a file, as evidence for IDs.

**CSV and TSV files.** One whose first line is "Shazam Library", or with a Shazam column such as TagTime or TrackKey, is read as the user's Shazams (`shazam:FILE` forces that for any CSV). Any other with Artist and Title columns is a song list named after the file; a Mix or Version column sets the mix. Spreadsheet cells piped in with their header row work the same way.

**When `set` listens.** With a link, `set` reads the description and chapters, then MixesDB when the page lists no tracklist (a MixesDB page counts only when it links the same upload, or names the same DJs and event with the same day or length). It listens to the set when the tracklist is missing, has IDs or MixesDB marks it incomplete, and when `--tracklist` gives one (to check and time it; the tracklist is kept for the set, and the next `--tracklist` replaces it), and reuses an earlier listen at no cost. Without the link, `djlib set -` takes the same list at once, with its own times but no links into the set. `--identify` always listens, `--no-identify` never. With a file, `--identify` or `--tracklist` need `--source URL`. Without song recognition, a link with a published tracklist uses that list, a warning says why and `listen_fix` holds the command that fixes it. How long listening takes: An hour-long set usually takes 5–10 minutes; when Shazam is busy it can take half an hour. Ctrl-C stops it; run the same command later to carry on. stderr then carries `{"event": "identify_started", "job_id", "url", "identify_reason"}` (`no_tracklist`, `ids`, `incomplete`, `asked` or `identified_before`) and the `identify_progress` events below.

### The set result

`tracks[]` lists every song in set order, Shazam lists too:

- `position`, `timestamp` (`mm:ss`, or null), `label` (IDs read `ID`), `artist`, `title`, `version` (null for IDs).
- `own`: `owned`, `missing`, `maybe` (a file with the same title and mix under a shorter or longer artist name: `maybe_owned`, its label, with `maybe_recording_id` and `maybe_asset_revision_id` for a `satisfy` pick once the user says it's the song), `other_mix` (the user has another mix: `you_own`, such as `["Radio Edit"]`) or `id`. `owned` includes songs owned twice (state `ambiguous`: pick a copy with `djlib ui`) and files djlib can't read right now (`unavailable`, often an unplugged drive); `in_crate` is true only for the rest.
- `state`: the saved state (`satisfied`, `missing`, `ambiguous`, `unavailable`, `source_selected`, `unknown`).
- `confidence`: for a set djlib identified, `confirmed`, `likely`, `weak`, `conflict` or `unidentified`; null otherwise.
- `notes`: why the song is in the set ("Shazam heard it at 05:39 (4×); also in MixesDB", "MixesDB spells it …", "Shazamed 2026-10-10").
- `matched_as`: the credits the user's file matched under when they differ from the shown ones, e.g. the listing's "Robert Glasper - Move Love (Kaytranada Edition)" for Shazam's "Robert Glasper Experiment". It is the same exact mix; the terminal shows "Owned under another spelling". `djlib set` sends every listing's spelling (MixesDB's and a pasted tracklist's) and the file may match any of them.
- `listen_url`: that moment on YouTube or SoundCloud (`&t=339s`, `#t=5:39`); null without a set link or time.
- `shop`: for `missing`, `maybe` and `other_mix`, `{beatport, bandcamp, traxsource}` store searches. djlib never opens or buys them.
- IDs only: `maybe`, the song djlib heard there or null; a guess, never the ID's name.

The rest of the result: `name`, `request_id`, `revision`, `source` (`url`, `provider`, `title`, `uploader`, `duration_seconds`), `mixesdb` (null, or `page_title`, `page_url`, `tracklist` `complete`|`incomplete`|`unknown`, `match`), `songs`, `counts` (`owned` including those waiting on a pick or a file, `to_check` those, `maybe`, `other_mix`, `missing` and `ids`, as `djlib requests` gives them; `missing` is what to buy), `owned` (songs going into the crate), `other_mix` (count), `missing[]` (every song not in the crate, other mixes and IDs included: `position`, `label`, `state`, `you_own`, `notes`, `confidence`), `owned_to_check[]` (crate songs whose `confidence` isn't `confirmed`), `id_hints[]` (what listeners named near each ID's time), `identified` (null, or `coverage` with `percent` named and `percent_heard`, `counts` with `heard` by Shazam, `listed_only`, `ids` and each status, `tracklist_file`, `job_id`, `reused`, `recognizer`, `listened_at`), `fetched`, `listen_fix`, `collection_id`, `crate_tracks` (how many files the crate holds: a song listed twice is in it once), `playlist`, `replaces` (the older playlist when a changed set became "Name (2)"), `rekordbox` (`status` `imported`, `already_in_rekordbox` or `skipped` with `reason` and `reason_code`; `installed` with `NOT_REQUESTED`), `usb` (`found`/`expected`, `in_order`, `analysis_from_device`, `player_playback_verified: false`), `complete` and `next` (commands that finish the job when `complete` is false). `set shazam…` adds `shazam` ([below](#shazam-history)).

A big list is big JSON: about 0.6 KB a song on one line, mostly `shop` links. Save it to a file and summarize it.

Checking a set again updates its saved list (same name, same songs in the same order; case, "feat." or "ft." and the order of credited names don't count) and keeps its crate and playlist. When the set now owns other songs, its crate changes and rekordbox gets "Name (2)" with `replaces`.

**The terminal view** shows the set as one table, in set order: # · Time · Track · Sure · Own. Sure: `✓ confirmed` (just `✓` below 100 columns), `◐ likely`, `! weak`, `✗ conflict`, `○ ID`. Own: `✓ owned`, `✗ missing`, `? maybe yours` (named under the table: "Maybe yours under another name: #2 …"), `≈` and the other mix you own, `! owned, pick a copy`, `! owned, file unavailable`, blank for IDs. The heading counts, e.g. "14 of 22 owned · 1 to check · 1 maybe yours · 1 in another mix · 7 missing · 2 IDs". Over 60 songs it lists the first 25 not owned yet and points to the shopping list. Next steps follow, such as "Put it in rekordbox", "Or straight onto your USB" and "Your shopping list (7)".

### Running rekordbox and USB steps

- Run `set`, `rekordbox push|usb|pull` in the background; `--usb` waits up to `--timeout` seconds (600) for the click and `--when-idle` waits for the user to step away. `--usb` can't be combined with `--when-idle` or `--no-rekordbox`; `--device /Volumes/NAME` picks the stick when several are mounted.
- Exit codes: 0 done, 2 input or other error (see `error.code`), 4 the USB check found missing or out-of-order files, 130 Ctrl-C (a listen stops; running it again continues).
- Without macOS, rekordbox or the Accessibility permission, `set` skips the rekordbox step (`rekordbox.status: skipped` with the reason) and still builds the crate.
- Events, click flow and rekordbox errors: [delivery](delivery.md#rekordbox-and-usb-from-djlib-set).

### Set errors

- `SOURCE_BROWSER_ONLY`: a 1001Tracklists link (also `1001.tl`). djlib can't read it. `details.next_steps` give the paste commands for people; `details.next_for_assistants` says what you do: pipe the tracklist into `djlib set 'LINK' --tracklist -` with the set's YouTube or SoundCloud link, or into `djlib set -` without it. Read the page in the user's browser only if you can; never fetch it or get around its captcha.
- Mixcloud (`SOURCE_UNSUPPORTED`): Mixcloud doesn't allow downloads, so djlib can't listen. Use the same set on YouTube or SoundCloud, or its tracklist.
- `TRACKLIST_NOT_FOUND`: no tracklist on the page or MixesDB, and djlib couldn't listen (or `--no-identify`). The message says why; `details.next` fixes what's missing, `details.tracklist_next` is the way to give a tracklist, and `details.named_in_comments` is what listeners named: a starting point, not the tracklist.
- `SET_PREVIEW_ONLY`: only a preview plays (SoundCloud Go+). Ask for the tracklist and pipe it into `djlib set - --source 'URL'`.
- `INPUT_INVALID`: `--tracklist` with `--no-identify`, `--identify`/`--tracklist` with `shazam`, a missing file, a folder, a link without `https://`, or text with no tracklist in it; `details.title` names it.
- `APP_PLAYLIST_NAME_TAKEN`: rekordbox has a different playlist with that name; rename one or pass `--name`.

## Every song in a set: `djlib identify`

```bash
djlib identify 'https://www.youtube.com/watch?v=VIDEO'                     # read, listen, merge
djlib identify 'https://soundcloud.com/USER/SET' --tracklist - < 1001.txt  # … with a copied tracklist
djlib identify 'URL' --again --recognizer audd                              # listen again, with AudD
```

`identify` reads the set's description, chapters and up to 1,000 comments, looks the set up on MixesDB, downloads its audio, has Shazam recognize 12 seconds every 30 (every minute when a tracklist times its songs), listens again where songs change, and merges everything into one timestamped tracklist. It doesn't check the user's music; `djlib set 'URL' --no-rekordbox` does, reusing this listen. Options: `--tracklist FILE` or `-` (repeatable; remembered for the set), `--again`, `--recognizer shazam|audd|acrcloud`, `--name`. One YouTube video or SoundCloud set per run; `youtu.be`, `&t=` and `&list=` don't matter.

While it runs, stderr carries `{"event": "identify_progress", "job_id", "phase", …}` at each phase (`queued`, with `ahead` when another set goes first, `reading_page`, `downloading`, `listening`, `checking_gaps`, `merging`) and at most every 15 s, ending with `completed` or `failed`. Progress has `position_seconds` of `duration_seconds` (how far into the set, while listening), `songs_heard`, `windows_done` of `windows_total`, `waited_seconds` and `eta_seconds`. While Shazam makes djlib wait, progress has `waiting` (`service`, `reason` `rate_limited` or `unreachable`, `retry_in`, `gives_up_in`) and `eta_seconds` is null: say Shazam is slow, and promise no time. `djlib jobs watch JOB_ID` shows the same. Ctrl-C cancels (exit 130, `JOB_CANCELLED` with `details.job_id`); what was heard stays, so running it again continues.

The result: `job_id`, `source` (`url`, `provider`, `title`, `uploader`, `duration_seconds`), `recognizer` (`name`, `licensed`), `listened_at`, `duration`, `coverage` (`percent` of the set with a named track; `percent_heard` and `seconds_heard` where Shazam heard the song placed there; `gaps[]` of 90 s or more it heard nothing in), `counts` (`tracks`, each status, `heard`, `listed_only`, `ids`), `tracks[]`, `unplaced[]` (songs heard once that fit no ID, with `at` and `listen_url`: not tracks), `sources[]`, `listed`, `mixesdb`, `tracklist_file` (text for `djlib set FILE`), `tracklist_json` (all evidence), `reused`, `notices`, `publisher_text` and `next`. `next` starts with `djlib set 'URL' --no-rekordbox`, and adds `djlib identify 'URL' --tracklist paste` (over MCP: `djlib_identify_set` with `tracklist_text`) while IDs, `weak` or `conflict` tracks are left (or under 70% is named) and the user gave no tracklist: offer to add one, piped into `--tracklist -`.

Each track: `n`, `start` (`mm:ss`), `approx` (time guessed between neighbours), `kind` (`named` or `unknown`), `artist`, `title`, `version`, `status`, `evidence` (one line: what Shazam heard and where it's listed, e.g. "Shazam heard it at 34:39 (3×); also in MixesDB; Shazam named it …"), `listen_url`, up to three `candidates` plus up to two listener suggestions (`{source: "audio", label, heard_by, windows, at, strength}`, `{source: "comments", label, mentions, likes, at}`, or the listing's song for a conflict, `listed_in`), for an ID in a long stretch nobody heard, `songs_estimate`, and for a confirmed song a listing spells another way, `also` (the spellings it is owned under, ready for a request item). Statuses:

- `confirmed`: Shazam heard it where a tracklist puts it, or heard it 3+ times.
- `likely`: one good source: on tracklists but not heard (however many list it, since they copy each other), heard twice, or listed and heard under another name (a remix heard as its sample; `evidence` says what each side has).
- `weak`: heard once, or only listeners' comments.
- `conflict`: a tracklist names one song where Shazam heard another.
- `unidentified`: an ID nothing named (the terminal says `ID`). An ID keeps `artist: null`; its candidates are guesses.

MCP: `djlib_identify_set` (`url`, `idempotency_key`, optional `tracklist_text` with `tracklist_source` `file` (default), `1001tracklists` or `mixesdb`, `again`) returns the job. Read `djlib_job` every `next_poll_after_seconds` (15 s while it listens, 30 while Shazam makes it wait): `result.progress` while it runs, then the result above. A repeated call with the same key gives the same shape. After a failure, call again with a new key: it continues. `djlib_control` with `cancel` stops it. A 1001Tracklists or Mixcloud link comes back with `details.next` naming the tool route. A finished job's `next_without_shell` says how to check the set against the music with `djlib_create_request`.

Errors:
- `DEPENDENCY_REQUIRED`: song recognition, FFmpeg or yt-dlp is missing; `djlib doctor` shows how to install it (song recognition: rerun the djlib installer).
- `RECOGNIZER_NOT_CONFIGURED`: AudD or ACRCloud keys are missing. Set them, run `djlib service stop` (the background service reads them when it starts), then retry. `RECOGNIZER_REJECTED`: Shazam refused djlib, or a licensed service refused the key or its quota is used up.
- `RECOGNITION_RATE_LIMITED`, `RECOGNITION_UNAVAILABLE`, `RECOGNITION_FAILING` (Shazam couldn't hear many parts of the set in a row, or over a quarter of it), `DISK_FULL`: what was heard is saved (`details.resumes: true`). Fix the cause if there is one, then run the same command later; it continues.
- `SET_PREVIEW_ONLY`, `SOURCE_BROWSER_ONLY`, `TRACKLIST_NOT_FOUND`: as for `set` above. `SOURCE_UNSUPPORTED`: not a YouTube or SoundCloud link (`djlib_source_inspect` answers a 1001Tracklists link with it too; its message and `next` say what works). `SINGLE_SOURCE_REQUIRED`: a playlist or channel page.

The first identification with Shazam (the default) in a workspace adds a notice: djlib uses Shazam's service the way the Shazam app does, unofficially, so it may slow down or stop working. Licensed services need the user's own account and keys in the background service's environment: `DJLIB_RECOGNIZER=audd` with `DJLIB_AUDD_TOKEN`, or `DJLIB_RECOGNIZER=acrcloud` with `DJLIB_ACRCLOUD_HOST`, `DJLIB_ACRCLOUD_KEY` and `DJLIB_ACRCLOUD_SECRET`. A set identified with one service is reused with a notice when another is asked for; `--again` listens with the new one. The audio is deleted after a successful listen (`DJLIB_KEEP_SET_AUDIO=1` keeps it). `DJLIB_MIXESDB=off` there turns MixesDB lookups off. Notices also say when the download came out short and when Shazam couldn't hear some minutes of the set (those parts count as not heard).

## Shazam history

```bash
djlib shazam status                               # where the history is, how many songs, what to check first
djlib shazam list --since 2026-10-01              # the songs, newest first (--from apple-music|PATH, --limit)
djlib set shazam:2026-09-14 --no-rekordbox        # since a date (status gives the last 30 days as recent_spec)
djlib set shazam:new --no-rekordbox               # since the last import
djlib set shazam --no-rekordbox                   # the whole history, oldest first, 1,000 at a time
djlib set shazam:apple-music --no-rekordbox       # Apple Music's "My Shazam Tracks" (opens Music)
djlib set ~/Downloads/shazamlibrary.csv --no-rekordbox  # a CSV from shazam.com (shazam:PATH also reads an iPhone backup or ShazamDataModel.sqlite)
```

`djlib shazam` alone is `shazam status`. `set shazam` reads this Mac's Music Recognition history, which holds the iPhone's Shazams when Shazam syncs with iCloud, and the older Shazam app for Mac. The set keeps the order they were Shazamed in; each song's notes say when and where (place names, no coordinates). Lists are named after their days ("Shazam 26 Sep – 12 Oct") or file ("Shazam (shazamlibrary.csv)").

- `shazam status` result: `ready`, `songs`, `newest_shazamed_at`, `recent_spec` (`shazam:` and the date 30 days back, or the 30 days before an older newest Shazam), `sources[]`, `last_import` (with `pending`, the rest of a list cut at 1,000 that `shazam:new` can't bring: `spec`, `songs`, `since`; and `newest_list`, the import holding the newest Shazams), `new_since_last_import`, `setup` and `alternatives`.
- `result.shazam` of a set: `songs`, `songs_in_history`, `since`, `first_shazamed_at`, `newest_shazamed_at`, `left_out`, `remaining` (songs a 1,000 cut left for later) and `next_spec` (the `set` argument that brings them: `shazam:new`, or `shazam:DATE` when a newer import moved the `shazam:new` mark). A cut list warns "This list has your oldest 1,000 Shazams (…); the other 520 come next."
- `shazam list` and MCP `djlib_shazam_history` (`source`, `since`, `limit` 1–1000) list songs newest first by their latest Shazam, read-only, with `recent_spec`.

Errors:
- `SHAZAM_HISTORY_NOT_FOUND`: no history yet. Read the user `error.details.setup` word for word: (1) On your iPhone, open Shazam → Settings → turn on iCloud Sync. (If it's off or greyed out: Settings → [your name] → iCloud → See All → turn on Shazam and Music Recognition.) (2) On this Mac: System Settings → [your name] → iCloud → See All → turn on Music Recognition. (3) Add Recognize Music to the menu bar and click it once (macOS 26: Control Center → Edit Controls → Recognize Music; macOS 14–15: System Settings → Control Center → Music Recognition → Show in Menu Bar). Your iPhone's Shazams arrive within a few minutes. Then `djlib shazam status` again. `details.alternatives`: `shazam:apple-music`, or a backup or CSV (shazam.com/myshazam → Download CSV, if their account shows it; shazam.com's list stops updating once Shazam syncs with iCloud).
- `SHAZAM_ACCESS_DENIED`: macOS keeps the history private. Allow the app the user runs djlib in (Terminal, iTerm, VS Code or Claude) under System Settings → Privacy & Security → Full Disk Access, quit and reopen it. `details.next` opens that setting.
- `SHAZAM_NOTHING_NEW`: nothing new since the last import. Not a failure: `details.next` shows the crate with their newest Shazams.
- `SHAZAM_BACKUP_ENCRYPTED`: use iCloud sync or an unencrypted Finder backup. `SHAZAM_HISTORY_UNREADABLE`: not a Shazam library djlib can read.
- `SHAZAM_MUSIC_NOT_ALLOWED`: allow the app to control Music under Privacy & Security → Automation. `SHAZAM_PLAYLIST_NOT_FOUND`: Music has no "My Shazam Tracks"; connect Apple Music in the Shazam app. `SHAZAM_MUSIC_UNSUPPORTED`: Apple Music is read on a Mac only. `SHAZAM_MUSIC_FAILED`: Music didn't answer; try again.

## Saved sets, shopping lists and crates

```bash
djlib requests                                  # the sets you checked, last checked first, with owned/missing counts
djlib requests get SET                          # each song, owned or missing (items carry `shop`)
djlib requests report SET                       # the shopping list: store links, saved as "<set> - to buy.txt"
djlib requests refresh SET                      # check it again, e.g. after a scan
djlib requests collect SET                      # a crate of the owned songs, in set order
djlib crates                                    # your crates, newest first, with rekordbox and USB
djlib crate [CRATE]                             # a crate's tracks (the newest by default)
```

SET is a set name, the start of its ID (6+ characters) or `last`; `--revision` defaults to the current one. `requests report` prints each missing song with its time and Beatport, Bandcamp and Traxsource search links (25 at most; the rest are in the file), the other mixes the user has, and the IDs. Its result has `name`, `to_buy[]` (`position`, `timestamp`, `label`, `shop`; a Shazam's songs add `shazamed`, the day, and print newest first), `maybe_yours[]` (with `maybe_owned`: check before buying), `other_mixes[]` (with `title`, `you_own`), `ids[]`, `shopping_list` (the text file) and `report_path` (the full JSON report). `requests list` rows (and MCP `djlib_requests`) carry `counts` (`owned`, `missing`, `maybe`, `other_mix`, `ids`, which add up to `total_items`, and `to_check`); `owned` counts songs owned twice or on an unplugged drive too, and `to_check` says how many, so every screen reads "1 of 4 owned · 1 to check". `requests get` (columns Own, Track, Your file, as `djlib set` says it) shows "as Robert Glasper" under a file matched under the listing's spelling. `requests collect` ends with "✓ Crate "Name" N tracks, in set order", or lists the songs it left out and why. Matching rules, JSON input and picks: [requests](requests.md).

## Library, status and jobs

```bash
djlib status                                    # library, sets with their crates, next steps (terminal)
djlib library Joy Orbison                       # search: every word must match artist, title or version
djlib library --query "Joy Orbison" --limit 100 [--after NEXT_CURSOR]
djlib scan [FOLDER]                             # index new music
djlib init ~/Music /Volumes/DJ                  # set up, or add folders to an existing setup
djlib roots add /Volumes/DJ                     # allow another music folder
djlib ui                                        # the review page: sets, crates and library
djlib doctor                                    # FFmpeg, YouTube helper, song recognition, rekordbox, service
djlib jobs get JOB_ID
djlib jobs wait JOB_ID --timeout 30
djlib jobs items JOB_ID --state failed
djlib jobs control JOB_ID pause|resume|cancel|retry
djlib source-inspect 'https://www.youtube.com/watch?v=VIDEO' [--comments 200]
djlib rekordbox sync                            # BPM and cues from rekordbox's analysis files, no window
djlib rekordbox pull --when-idle 60             # adds key through one XML export (uses rekordbox's window)
djlib schemas                                   # strict JSON inputs
```

`status` JSON has counts, `recent_requests[]` (with `counts`, `to_pick` and `in_crate`), `recent_collections[]` (with the `request_id` each crate came from), `last` (what `last` names: the set checked most recently and its crate), `shazam_history_found`, `rekordbox_app` (installed here) and `rekordbox_open` (present when its playlists couldn't be read); its next steps are in the terminal view only. `library` pages with `next_cursor` passed as `--after` and the same query until it is null. `jobs wait` exits 3 while the job is still running and 4 for attention, failure or a partial outcome. Jobs survive the CLI or MCP process; cancelling keeps files already accepted. `djlib requests`, `shazam`, `roots` and `jobs` alone list or show status. `djlib search` is `library`. `SOURCE_NOT_ALLOWED`: a file outside the user's music folders; `details.path` is the folder for `djlib roots add`. `djlib ui` gives the review page's link (`opened` says whether a browser opened); the link carries a private access token, so don't share it. Notes, BPM/key, filtered crates and changed files: [organize](organize.md).

MCP equivalents: `djlib_library`, `djlib_requests`, `djlib_collections` (crates), `djlib_collection` (a crate's songs), `djlib_source_inspect` (`comments: 200` adds listener comments; `mixesdb` is MixesDB's tracklist when the page lists none), `djlib_identify_set`, `djlib_control`, `djlib_shazam_history`, `djlib_find_sources` (ranked YouTube and SoundCloud uploads for one song), `djlib_download`, `djlib_create_request`, `djlib_collect_request`, `djlib_import_rekordbox_analysis`. The rekordbox and USB steps are CLI-only.

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
djlib download --file downloads.json
```

URLs are placeholders; `djlib_find_sources` (or `set --fetch`) finds and ranks real ones. `set --fetch` downloads only confident matches; show `fetched.needs_your_pick` with the best guess and let the user choose, then `download --file` with that URL and the requested artist, title and version. Optional `alternates` (up to three URLs of the same recording) are tried in order when the first upload is gone. Public YouTube, SoundCloud and Bandcamp recordings only, up to 30 minutes each and 1,000 per request. Downloads are MP3 at the source's quality, tagged with the requested artist, title and mix, and their Comments read "Web rip from YouTube (stream quality) — buy before playing out" with the link, so they stand out in rekordbox. They need the `download` extra and FFmpeg; YouTube also needs Deno or Node 22+.

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
djlib plan --file request.json
djlib start PLAN_ID --revision 1 --key friday-v1
djlib export COLLECTION_ID --key friday-export-v1
djlib usb-preflight /Volumes/DJ_USB --required-bytes 1000000000
```

`club` indexes in place; `archive` copies accepted audio into the workspace. `export` writes a hash-checked manifest, an M3U8 and an experimental rekordbox XML; for rekordbox itself prefer `rekordbox push`. `usb-preflight` only reads free space. Working copies for rekordbox or Serato: [delivery](delivery.md).

## Service and startup errors

Commands start the background service on demand, and it stops after 30 idle minutes; `djlib service start` starts one that keeps running. `service status` inspects it and `service stop` stops it; accepted work stays saved and resumes on the next start. A copy of a workspace runs its own service.

- `SERVICE_START_BUSY`: another starter held the lock for 45 seconds. Check `service status` before retrying.
- `SERVICE_START_FAILED`: the service exited. Read the workspace's `runtime/service.log`, fix the cause, then retry.
- `SERVICE_START_TIMEOUT`: no healthy service within 30 seconds while the process still ran. Check status and the log before retrying; don't launch repeatedly.
- `COORDINATOR_VERSION_MISMATCH`: an older service is still busy. When its jobs finish, `service stop`, then retry.
- `CLIENT_OUTDATED`: djlib was updated under this session. Ask the user to reconnect djlib (Claude Code: `/mcp` → djlib → Reconnect) or restart the assistant; don't stop the service or retry.
- `COORDINATOR_START_REQUIRED` (Windows MCP): run `djlib service start` in an ordinary terminal, then reconnect.

None of these submits a music job. A later uncertain submission still uses its original idempotency key. Redact logs before sharing.
