# Changelog

## Unreleased

From a live `set --fetch` run where the official Phoenix – Lasso upload had become DRM-only:
- **Only uploads that play are trusted.** `--fetch` and `find_sources` check the best three uploads of a song (in parallel, 30 s at most) before calling one a clear match. Removed, region-locked and DRM-protected uploads are dropped and listed under `unavailable`, and the rest are ranked again.
- **Downloads fall back.** Each chosen song carries up to three other uploads of the same recording (`alternates`: the artist's or label's own, or the same length); when an upload turns out to be gone, the next one is downloaded. Download items say which URL arrived (`source_url`) and which were tried (`tried_urls`). `djlib_download` accepts `alternates` too.
- **Label uploads count.** SoundCloud's artist credit names the artist even when the title doesn't, and a label upload the length of the artist's own upload (e.g. Glassnote's Lasso) can be a clear match, also when the artist's upload is gone.
- **Gone is gone.** DRM-protected, region-locked and removed uploads fail as `SOURCE_UNAVAILABLE`, which is not retried; `--fetch` retries a download only when a failure is retryable.
- **No false metadata conflicts.** A file tagged “Lasso” is accepted for “Lasso (Original Mix)”, and “Antdot, Maz” for “Antdot & Maz (BR)”, as in request matching; another mix, a live take or another artist still asks for review.
- **No empty download crates.** A download's crate is made when its first song arrives, and repeated `--fetch` runs of one set add to one “<set> — downloads” crate.

## 0.1.0a13

From an end-to-end test run of the Claude Code plugin:
- **A locked Mac is handled quickly and clearly.** Assistants get `{"event": "unlock_mac"}` on stderr at once and `APP_SCREEN_LOCKED` after 30 s instead of a 10-minute silent wait (a terminal still waits visibly). A skipped rekordbox step carries `reason_code`, `set` results say `complete` and `next` (the exact command to finish), and `doctor` shows whether the screen is locked.
- **`--fetch` and `find_sources` agree.** Searches leave out “(Original Mix)” and country tags, which pushed official uploads out of the results (e.g. Phoenix – Lasso on SoundCloud).
- **Re-checks keep the revision** when nothing changed, so printed commands stay valid.
- **Sets without a tracklist** return what listeners named, in set order with times, in `error.details.named_in_comments`; `source-inspect --comments N` and `djlib_source_inspect` return the same list.
- **`crates` shows each crate's rekordbox/USB record**, matching `status`.

## 0.1.0a12

- **Updates just work.** After an update, an older background service that is idle is replaced automatically and the command goes through; only a busy one gets a short note saying to run `djlib service stop` when it's done.
- **Rerunning a set is safe and current.** The same tracklist is re-checked against your library (songs you added since count as owned); an unchanged set reuses its crate and playlist, and a set that gained or lost songs goes into rekordbox as “Name (2)” instead of clashing with the playlist djlib made before.
- **“(Original Mix)” means no special version**: it matches files tagged with the bare title and vice versa; a Radio Edit or remix is still another version.
- **USB failures are failures.** `rekordbox usb` and `set --usb` exit with code 4 when files are missing or out of order. In JSON mode, the click prompt is written to stderr as `{"event": "select_playlist", …}` so an assistant running djlib in the background can tell you which playlist to click.
- `set --when-idle SECONDS` defers the rekordbox import until you are away; `--yes` without `--fetch` and `--usb` with `--when-idle` or `--no-rekordbox` are explained instead of ignored. Titles containing `[` or `]` no longer break the terminal output.
- **`set` works without rekordbox.** Owned/missing, the crate and `--fetch` no longer need rekordbox: when it can't be driven (Linux/Windows, not installed, no Accessibility permission) the rekordbox step is skipped with the reason shown; `--no-rekordbox` skips it on purpose.
- **Faster.** Every command starts about 0.4 s sooner (`djlib version` 0.85 s → 0.2 s) because the CLI no longer loads server code; `--fetch` searches four songs at a time (a 13-track set: 31 s → 11.5 s).
- **Install and update in one line.** `curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh` installs uv if needed, the newest release (bytecode precompiled, so the first command is quick) and FFmpeg via Homebrew, then runs `doctor`. `djlib upgrade` installs the newest release and restarts the background service (`--check` only looks).
- **Claude Code and Codex plugin.** `claude plugin marketplace add uneasymusings/dj-library-tool` then `claude plugin install djlib@dj-library-tool` (or `codex plugin marketplace add …` / `codex plugin add djlib@dj-library-tool`) installs the MCP server and the skill together. The MCP server starts even before `djlib init` and answers with the init command.
- **Leaner for assistants.** MCP exposes 19 core tools by default (`DJLIB_MCP_TOOLS=full` for all 43): tool definitions shrink from 66k to 27.5k characters, and replies are compact JSON. The skill is about 1,100 words instead of 3,000, with details in references. `capabilities` now reports rekordbox automation, USB export, search and downloads accurately and lists the CLI-only commands.
- **A DJ-sized command line.** `djlib --help` shows about 15 commands in Start here / Your library / rekordbox and USB / Settings / Assistants / More; legacy commands still work and are listed in docs/ADVANCED.md. `crates` and `crate` are the names (`collections`/`collection` still work); `djlib --version`; `-w PATH` works anywhere.
- **Short handles.** Crates and request lists accept `last`, their name, or the first 6+ characters of the ID (`djlib crate last`, `djlib rekordbox usb "Friday"`); `--revision` is optional on `requests refresh` and `report`.
- **Onboarding.** `doctor` works before `init`, shows the fix for each failed check (e.g. `brew install ffmpeg`) and exits 1 when FFmpeg is missing; `init` and `scan` warn about FFmpeg up front; `scan` without a path scans every music folder; libraries up to 100,000 files scan in one go (was 10,000). Tracklists accept trailing timestamps (`Artist - Title @ 41:20`) and an unnumbered first line as the set's name.
- **Clearer output.** Errors open with a plain title (“djlib isn't set up yet”, “You didn't pick the playlist in time”) and the code dimmed; long lines wrap under their label; tables keep names and mix versions and show 8-character IDs; one Next block per screen, starting with the step that matters (build the crate, put it on USB); scan failures are grouped by reason; `status` flags crates that are older than their tracklist; a failed USB check is the headline (“don't take this stick yet”) with the unmatched tracks listed.
- The skill's CLI reference covers `set` (files and set links), `--fetch`, `--usb`, `rekordbox usb`, the new error codes and the MCP equivalents.

## 0.1.0a11

- **Sets from a link.** `djlib set https://soundcloud.com/…` or a YouTube set reads the tracklist from the upload's description or chapters, and reads up to 1,000 listener comments: for each “ID” with a time, the screen shows what listeners named around that moment (SoundCloud timed comments; timestamps written in YouTube comments). Hints are evidence only and are never added by themselves.
- **Uploads without a tracklist** (most Boiler Room videos) say so and list what listeners named in the comments, e.g. a fan's timestamped tracklist, as a pointer. Comment mining ignores reactions with a single timestamp, time ranges and sentences that merely contain a dash.
- **`--fetch`: missing songs as MP3.** Searches YouTube and SoundCloud for each missing song, ranks uploads (official artist uploads first; 30-second previews, live recordings, full sets, sped-up versions and unrequested remixes dropped or ranked down), shows the clear matches and downloads them as MP3 after you confirm (`--yes` for scripts). Unclear ones are listed with the best guess, not downloaded. The set is re-checked and the crate built with them.
- **Artist country tags.** Credits such as “Maz (BR)” or “Samm (BE)” match uploads and tags written without the tag, in search, ranking and request matching.
- **MP3 downloads.** Web downloads are saved as MP3 (VBR V0, or copied when the source already is MP3) instead of FLAC, so every CDJ plays them, and the library copy is tagged with the artist and title you asked for. A download the provider refuses is retried once. Quality is the source's; djlib labels them as unverified web audio.
- **1001Tracklists.** Its tracklist pages are only served to browsers, so djlib does not fetch them: paste the page's tracklist into a file (track-number lines and `w/` entries are understood), or let your assistant read it in your browser.
- **Assistants.** New MCP tool `djlib_find_sources` (43 tools); `djlib_source_inspect` can include comments.
- `rekordbox usb` and `set --usb` show how many tracks' key/BPM were read back from the stick.

## 0.1.0a10

- **`djlib set tracklist.txt [--usb]`: the whole workflow in one command.** Checks which songs you own, builds the crate in set order, imports it into rekordbox and, with `--usb`, exports it to your stick and verifies it. One screen shows what made it, what is missing and which other versions you own. Rerunning reuses the list, crate and playlist.
- **`rekordbox usb ID`: crate → USB stick (macOS).** Pushes the crate if rekordbox lacks it, asks you to click its playlist once (rekordbox's browser cannot be scripted), confirms the selection from rekordbox's own export dialog, runs Playlist > Export Playlist > your stick, waits until every file has arrived, then reads the playlist back from the stick's own device library (`export.pdb`, what CDJs load) and checks that each entry, in order, points to a file with your track's exact bytes. Nothing but the requested playlist is exported; djlib never writes the stick itself. The stick is found automatically, or pass `--device`.
- **Clearer next steps.** `status` suggests `set` for a new library, `rekordbox sync` when BPM is missing, and USB export for crates already in rekordbox, and remembers when djlib pushed each crate and last checked it on a stick. Scans, crates and collection views now point to `set`, `rekordbox push` and `rekordbox usb` instead of the older delivery and XML steps.
- **`doctor` checks rekordbox.** It shows the installed version, whether your terminal may control apps (with the setting to change if not), and whether rekordbox's analysis folder was found.
- **Artists in any order.** Request lists match owned songs when a tracklist credits the same artists in another order or with other separators (`A, B` / `B & A` / `A x B` / `A feat. B`, including `Title (feat. B)`); a subset of the artists never matches. Other versions are found the same way, so “you own: Jamie Jones Remix” appears for a reordered credit. A trailing `[ft. X]` in a tracklist is kept as a featured artist rather than dropped as a label.
- **Tracklists.** Lines numbered `03 Artist - Title` (no dot) parse when the numbers run in sequence, while artists like `2 Unlimited` stay intact. Rerunning a tracklist that a newer djlib parses differently creates a new list instead of an idempotency error.
- **Upgrades.** When an older background service is still running after an update, the message names the exact `service stop` command for your workspace.
- **Key from the stick.** A verified USB export also brings rekordbox's key and BPM for those tracks into your catalog, read from the stick's device library, so no XML export window is needed for them.
- **Never the wrong playlist.** A rekordbox playlist with the crate's name is trusted only if djlib pushed that crate there; otherwise its tracks and order are checked against one XML export first. If it differs (an older version of the set, or your own playlist), djlib stops with `APP_PLAYLIST_NAME_TAKEN` before importing or exporting anything.
- **Locked screen.** rekordbox automation reports `APP_SCREEN_LOCKED` instead of a confusing focus failure, and `--when-idle` waits until the Mac is unlocked as well as idle.
- **macOS download cleanup.** Stopping a finished download no longer fails with `PermissionError` when its process group has already exited (an intermittent macOS CI failure).
- **Unreadable menus.** When rekordbox's playlist menu cannot be read, `push` learns and confirms playlists from one XML export instead of importing again.

## 0.1.0a9

- **No more `--workspace`.** The first `init` becomes your default workspace; `djlib use PATH` switches it and `djlib use` shows it (`DJLIB_WORKSPACE` still wins). Stored in `~/.config/djlib/workspace` (Windows: `%APPDATA%\djlib\workspace`).
- **`djlib status`.** One screen with tracks, BPM/key coverage, recent request lists (owned/missing, lists needing a re-check), recent crates and whether each is already a rekordbox playlist (read from rekordbox's menu without focusing it), plus the next commands to run. Backed by `GET /summary`.
- **`rekordbox pull --when-idle SECONDS`** waits until you are away before the brief XML export that brings in musical key.

## 0.1.0a8

Background by default: less of the user's screen and time.

- **rekordbox analysis in the background.** `rekordbox sync` reads rekordbox's own analysis files (beat grid and cues, read-only, no window and no database access), matched by unique file name; after the first sync the coordinator repeats it every two minutes, incrementally (about 0.1 s for 1,160 files). On a real library its BPM matched rekordbox's XML for 959 of 959 comparable tracks. Key still comes from `rekordbox pull` (one brief XML export). `djlib_import_rekordbox_analysis` without a path performs the sync.
- **Faster, quieter `rekordbox push`.** One import per crate, confirmed by reading rekordbox's menu without focusing it; no XML polling (`--verify` adds a single export). Push several collections at once, push a request list directly with `--request`, and defer until the keyboard and mouse are idle with `--when-idle SECONDS`. Existing playlists are not imported twice; Unicode playlist names are kept. A request list became a rekordbox playlist in 9.5 s with rekordbox in front for 8.8 s.
- **Faster scans.** Each new file is decoded once instead of twice (validation and fingerprint share one decode), bytes already in the catalog are not decoded again, and up to four files are decoded ahead in parallel.
- **Untagged files.** Scans label untagged files from names like `03 - Artist - Title (Mix).mp3` (identity stays byte-based); rescans relabel earlier “Unknown artist” entries; request lists match them as `file_name_labels`.
- **Sturdier discovery.** A failed health probe is retried briefly (0.2 s, then 0.5 s) before djlib concludes the coordinator is gone, unless nothing is recorded or a different coordinator answered. Discovery now records why it failed, and the Windows lifecycle test reports that reason first. The intermittent Windows failure seen since a6 has no proven root cause yet.
- **Coordinator upkeep.** A coordinator started implicitly by a command exits after 30 idle minutes with no queued or running jobs; `service start` (used before assistant sessions) keeps running.
- **Assistant fast paths.** The skill now opens with one-command flows and computer etiquette (`--when-idle`, batching, no UI polling).

## 0.1.0a7

Tracklist-to-crate workflow, rekordbox analysis import, terminal experience, local review page and everyday catalog fixes. The JSON envelope, MCP tools and HTTP routes are unchanged for captured output.

- **Tracklist → crate.** `requests create --text FILE` turns a pasted tracklist into a request list (numbering, timestamps and labels stripped; headings name the list; skipped lines reported). Missing songs show which other versions you own. `requests collect ID` (MCP `djlib_collect_request`, `POST /requests/{id}/collection`) queues an ordered collection of the owned songs.
- **djlib drives rekordbox (macOS).** `rekordbox push COLLECTION_ID` imports a crate through rekordbox's own File > Import > Import Playlist (pointing at your original files, so existing analysis and cues are reused), waits for rekordbox's analysis, verifies the playlist's members from File > Export Collection in xml format, and pulls BPM/key into the catalog; `rekordbox pull` refreshes analysis. No direct database access; keystrokes are sent only when rekordbox and the expected dialog have focus. Verified live on rekordbox 7.2.19: a 28-track and a 55-track crate (55/55 analyzed).
- **rekordbox analysis import.** `import-rekordbox XML` (MCP `djlib_import_rekordbox_analysis`, `POST /analysis/rekordbox`) reads BPM/key from a rekordbox Collection XML export, matched by exact file path to originals or prepared working copies, as unverified `rekordbox_analysis` annotations that never overwrite values you set. Library rows and collection pages include a `dj` summary, and terminal tables show BPM/Key.
- **Large libraries:** `requests create/refresh` keep hash-checking matches in bounded batches until each is verified, instead of stopping at the per-call 1 GiB budget; the terminal shows such matches as “not checked” rather than unavailable. A real 708-track library (mostly 24-bit FLAC) indexed in 11 minutes with no failures.
- **`delivery plan` flags:** `--collection`, `--workflow`, `--app-version` (plus `--name`, `--player`, `--full`) replace the JSON file for the common case.
- **Readable terminal output.** In an interactive terminal, commands render tables, job cards, delivery stage checklists and copy-pasteable next steps; errors go to stderr with a recovery hint. Output stays the JSON envelope when piped or captured, with `--json` (before or after the subcommand) or with `DJLIB_OUTPUT=json`. Generated assistant sessions set `DJLIB_OUTPUT=json`.
- **Live job progress.** `scan`, `start`, `export`, `delivery prepare`, `delivery verify-app`, passed analysis observations and `organize collection` follow their job with a progress bar in a terminal; Ctrl-C detaches and the job keeps running. New `jobs watch ID` follows any job until it finishes.
- **Fewer manual steps in a terminal.** `--key` is generated when omitted (JSON output still requires it); `scan` without a path uses the only allowed root; `delivery observe ID` without `--file` asks what you saw and fills counts and recording IDs from the frozen manifest after you confirm.
- **Review page.** `djlib ui` opens a local browser page for the library (with BPM/key readouts), request lists (owned, missing, other versions, pick a version, build a crate), collections, delivery checklists and jobs. Only its static files load without the token; data calls use the existing authenticated routes under a strict Content-Security-Policy.
- **Grouped help** along the workflow (start here, library, requests, DJ app and USB, jobs, assistants) with shorter command descriptions.
- **Search** matches every word and ignores case and accents (`bjork` finds Björk, `royksopp` finds RØYKSOPP); the library is ordered by artist and title instead of internal IDs.
- **Mix names in titles.** “Rain (Extended Mix)” matches a request for “Rain” + “Extended Mix” (`equivalent_labels`), and building a collection from identical bytes with equivalent labels reuses the cataloged recording instead of failing. Different mixes still never match.
- **Scans** read WAV RIFF INFO artist/title, skip djlib's own workspace folders when the workspace sits inside a music root, and skip links that leave the allowed folders instead of failing the whole scan. Skipped counts appear in `result.skipped_files`.
- **Fixes:** a busy coordinator is no longer reported as absent: discovery waits up to 5 seconds for its health reply instead of 1 (a stopped coordinator still fails immediately), addressing intermittent Windows lifecycle-test failures; relative CLI paths resolve in the CLI's working directory rather than the coordinator's; `jobs wait` on a paused job exits 4 instead of 0; `jobs items --state` rejects unknown states; `demo` refuses to add tones to a real library; M3U labels keep the version; `usb-preflight` gives a next step that matches its result; clearer `WORKSPACE_REQUIRED`, `DELIVERY_STALE`, `EXISTING_IDENTITY_CONFLICT` and not-found messages; Ctrl-C prints a short note instead of a traceback; tracebacks no longer show local variables.

## 0.1.0a6

Published [v0.1.0a6](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a6) on **2026-10-05 at 07:14:53 UTC**. The [release matrix](https://github.com/uneasymusings/dj-library-tool/actions/runs/37275004527) passed all six OS/Python jobs and artifact packaging. An independent anonymous public installation verified package hashes, installed bytes, complete skill resources, actual MCP calls with all 40 tool schemas exposed and three original tones. An installed a3-to-a6 demo-copy compatibility check preserved all existing data; no schema migration was needed. Local source validation passed 518 tests with five Windows-only skips; Ruff passed 96 files. See [validation](docs/VALIDATION.md) for exact evidence and the earlier unresolved Windows discovery failure; subsequent passing matrices do not establish its cause.

- Startup waits for authenticated coordinator readiness for up to 30 seconds under a 45-second startup lock, instead of the earlier 12-second readiness window.
- Startup reports `SERVICE_START_BUSY`, `SERVICE_START_FAILED` or `SERVICE_START_TIMEOUT` with distinct recovery guidance. It does not resubmit a music operation or repeatedly spawn children within one attempt.
- Corrected the Windows MCP SDK error assertion and clarified that lossy compatibility encoding can reduce fidelity.

The 40-tool API and external Windows coordinator-start workflow are retained. a4 and a5 tags remain immutable, unpublished candidates; their historical tests are not final a6 release proof.

## 0.1.0a5 — unpublished candidate

Local validation passed 513 tests with five Windows-only skips; Ruff passed 96 files and the installed a5 wheel passed actual MCP calls with all 40 tool schemas exposed and three original tones. The release candidate failed Windows checks involving an incorrect SDK `is_error` assertion and the coordinator not becoming ready within the 12-second window. The underlying cause of the startup delays was not established. The tag is preserved and unpublished; no public artifact audit passed. The 40-tool API and catalog/delivery features below carry forward from the unpublished a4 candidate; its checks do not validate the Windows startup fix.

- Generated Windows `launch.py` starts the workspace coordinator before launching the assistant host. Manual MCP registration requires an external `djlib --workspace PATH service start` first.
- Windows MCP cold start returns `COORDINATOR_START_REQUIRED` rather than spawning a coordinator inside the host's Windows Job Object. The engine does not try to escape that job or its lifecycle controls.
- Delivery guidance requires the app's About version or native XML version rather than treating bundle build metadata as the actual app version.

The a4 tag is preserved but unpublished: its release was cancelled after confirming that MCP SDK cleanup could terminate a coordinator spawned inside the Windows job. See [validation](docs/VALIDATION.md) for historical candidate evidence and remaining gates.

## 0.1.0a4 — unpublished candidate

Local validation passed 498 tests with four Windows-only skips; Ruff checks passed for 95 files. The installed-wheel smoke check passed actual MCP calls with all 40 tool schemas exposed and three original tones. The production-change matrix passed all six jobs, but the later release candidate was cancelled after the Windows coordinator-lifetime blocker. Tag `039f1ca` remains unchanged and unpublished; no public a4 artifact audit passed. Historical a3 evidence is a separate baseline.

- Added paginated catalog and saved collection/request/delivery discovery, explicit additive music-root configuration, and field-level CLI input errors. Collection native state is `not_tracked_here`, not an inferred import/export result.
- Corrected new recording identity handling for symbol-only labels and untagged files. Existing incorrect merges are not automatically repaired.
- Added explicit durable reconciliation for changed catalog locations: `tag_only` checks decoded audio and stream properties; `replace_audio` preserves history and uses the new bytes' identity. Old collection revisions and annotations are not silently upgraded or inherited.
- Moved organization, passed analysis/native-export observations and app verification into durable jobs with item progress and generation-fenced evidence commits. Native-check retries derive their key from the complete intent; completed receipts point to saved delivery evidence, not a fresh readiness result.
- Allowed full local preparation without a physical pilot. Invalid supplied pilot claims still fail; native/device/playback readiness gates remain. Pilot and full working copies remain separate.
- Added bounded, read-only Windows partition-style queries; query failures preserve known volume identity and leave partition style unknown.
- Added checked catalog backups before automatic schema upgrades. This backs up the engine catalog only, not music or native DJ databases, and does not provide a one-command restore workflow.

No new native Serato, real-music, USB export or hardware playback validation is claimed. See [status](docs/STATUS.md) for pending release checks.

## 0.1.0a3

This alpha separates local library preparation, native app import, native USB export and hardware playback. A file transfer or M3U/XML artifact cannot mark a DJ USB ready.

- Added app-only rekordbox and Serato delivery workflows that need no player/controller model. Player-specific USB preparation remains a separate workflow.
- Added frozen collection/annotation snapshots, isolated working copies, format checks, explicit native observations, fresh app-file verification and read-only device identity/hash checks.
- Added read-only native rekordbox XML comparison for exact paths, playlist membership/order and exported app version. This compares a snapshot; it never advances readiness or opens media paths from XML.
- Added durable exact-recording requests, owned-file matching, explicit unknown/ambiguous versions, selected-source outcomes and missing-track reports.
- Added revisioned annotations, BPM/key provenance, notes/categories/roles/energy and ordered collections from existing catalog IDs. Explicit metadata travels to working-copy tags/comments; originals remain untouched.
- Added fair job scheduling, supported JavaScript runtime selection and 34 MCP workflow tools with corresponding JSON CLI commands and strict input contracts.
- Added explicit coordinator version checks before requests, preventing a new CLI from silently using an older running engine while preserving status/capabilities/shutdown access.
- Added database migrations preserving the a2 catalog and packaged assistant setup/skill resources. CI requires FFmpeg/ffprobe on Linux, macOS and Windows across Python 3.12/3.13; release drafting waits for this matrix.
- Corrected Windows temporary-copy flushing and file identity checks. USB readback uses held directory/file handles and rejects reparse points; filesystem and hardware validation remain separate from CI.

Before upgrading, stop the coordinator for the library workspace and preserve a backup of its engine catalog/configuration. Installation does not replace music or native DJ databases. Start the updated engine and run `doctor`; packaged migrations apply when its catalog opens. Regenerate a separate assistant session to receive the new tools and skill. Existing sessions/configuration remain unchanged.

Native import, musical BPM/key/grid review, cues, native export and eject remain actions in rekordbox/Serato. Serato native import, physical USB export and player playback need separate supervised validation. This release does not add automatic acoustic set recognition, exhaustive artist catalogs, Soulseek, automatic genre/energy inference or a standalone assistant model. See [coverage](docs/COVERAGE.md) and [validation](docs/VALIDATION.md).

## 0.1.0a2

Published the local engine, JSON CLI, 16 MCP tools, migrations, complete assistant skill, public selected-recording downloads, explicit collections, generic handoff artifacts and read-only USB preflight. The release included original-tone demos and separate Codex/Claude session setup.
