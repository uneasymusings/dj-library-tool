# Implementation status

## 0.1.0a16

A product pass on the two headline jobs: a set link to every track, owned or missing, and your Shazams as a set. It adds the whole set on screen, a shopping list with store searches, `djlib set paste`, recent Shazams first, a Sets view on the review page and listening progress in set time, plus matching fixes that count more owned songs. See the [changelog](../CHANGELOG.md#010a16).

How it was built and checked: a product review of 0.1.0a15 through seven lenses (set link, docs, assistant, first run, review page, Shazam, DJ market) gave a plan, which a critic cut and corrected. Seven streams built it (matching, set views, input, Shazam, listening, CLI, review page). Each stream's work was reviewed independently, with its own sandbox runs, before merging: 68 findings, with the high and medium ones fixed and covered by tests, except the MixesDB question below. After merging, five QA lenses (newcomer, review page, assistant, docs accuracy, merge review) found 70 more (3 high, 20 medium); a polish pass took the low ones; then a code review, a Windows-portability review (9 tests that would have failed on Windows) and two DJ walkthroughs checked the result, and what they confirmed was fixed. A few low ones remain, such as the upgrade's finishing step starting Python from the current folder (`python -m` without `-P`), and paging through `djlib requests` while a set is checked again (that set moves to page one).

Verified on 2026-10-14, on the release branch:
- **Live listen with this build.** `djlib set 'https://www.youtube.com/watch?v=cmgN8X7Pp_0' --no-rekordbox` (Keinemusik radio show, 62 minutes), in a fresh sandbox with Shazam: 418 s end to end (download, a window every 60 s, 31 checks where songs change, merge), with no waits for Shazam. All 12 named songs of the answer key, in order, within seconds of their starts, plus the opening ID and the ID at 48:30, whose first guess is the right one (Chris Fortier & Neil Kolo – All I Got, “weak”: heard once). Progress read “Listening… 8:00 of 1:02:09 · 1 song so far”, then “Checking the transitions… 16 of 31 · 8 songs so far”. Times were terminal links to the moment on YouTube.
- **Tests.** The full suite passes on macOS with Python 3.12 (1,555 passed, 5 Windows-only skips); Ruff check and format are clean. The suite also passed under simulated Linux and Windows conditions. A freshly built 0.1.0a16 wheel passes the installed-wheel check (45 MCP tools, 22 by default, skill packaged, request ledger checked), and the plugin manifest carries the same version.
- **Sets, in a sandbox with 22 tagged MP3s** and identifications cached from the 0.1.0a15 live runs (no new listening). Kaytranada at Boiler Room Montreal: 14 of 22 owned (12 in 0.1.0a15; #2 Robert Glasper and #19 Armand Val Helden were matched under MixesDB's spelling), 1 in another mix (Poison, Radio Edit), 7 missing; each track carries its SoundCloud moment (`listen_url`); it reused the list 0.1.0a15 had saved for that set. Its shopping list printed 7 songs with Beatport, Bandcamp and Traxsource searches and saved “… - to buy.txt”. Keinemusik (`djlib identify`): 13 tracks · 8 heard by Shazam · 3 from tracklists only · 2 IDs. A Shazam CSV given as a file: 2 of 3 owned. A piped tracklist kept its untimed ID at #2. `djlib set` alone showed the three ways in, and `djlib shazam status` with no history showed the three setup steps.
- **Re-running 0.1.0a15 sets** (stream review, sandbox copy of a 0.1.0a15 workspace): Kaytranada updated its 0.1.0a15 list in place (same request, revision 1 → 3, no second list); Keinemusik matched its 0.1.0a15 key unchanged and kept its crate.
- **Shazam** (stream review, a synthetic 1,520-song history): the last 30 days first, then the whole history ending in “The next 520 Shazams”, which brought 521 songs (520 new, 1 repeat); “Nothing new” pointed to a crate that opened; without Full Disk Access (folder set to no access), status showed its own headline and step.
- **Review page** (stream review, screenshots at 1440 and 390 px, light and dark): no sideways scroll at 390 px on any page; the 13-track set page is under 1,800 px tall on a phone.
- **Terminal** (stream reviews, 50 to 140 columns and a real pty): set and identify times are clickable links in a pty, and the set, shopping list, status, error and installer lines were read at 80 columns.
- **Upgrade** (stream review): the finishing step ran end to end against a sandboxed `uv tool` install with a fake `uv`, in pretty and JSON output, and tests show the old process imports nothing after the install.
- **MixesDB** (one fetch of its robots.txt): `Crawl-delay: 4` confirmed; lookups now space requests 4 seconds apart.

Not verified yet:
- **The “Shazam is slow” wait in a live listen.** Shazam didn't make the live listen above wait; the wait line and the 30-second polling during it were checked against recorded progress and tests.
- **Owner check: Shazam history on the owner's Mac (macOS 26.0, 25A354).** Not done before release. Do the three setup steps on the owner's iPhone and Mac, Shazam one song, then confirm that `~/Library/Application Support/com.apple.shazamd` appears and `djlib shazam status` lists the song. That folder was still missing on 2026-10-14, because Music Recognition hadn't synced there; the system's sandbox profile still grants shazamd that path. The CSV and Apple Music ways in don't depend on it. If it fails, take `djlib shazam status` out of the help's “New here?” line, the installer's closing lines and the README's ways in, and update the help test in `tests/test_cli_ux.py`.
- **MixesDB's robots.txt also has `Disallow: /*?action=`.** Read literally, that covers djlib's lookups (`/w/api.php?action=query&…`). They are a few user-triggered requests per set, 4 seconds apart, with an honest User-Agent, not crawling. The owner should ask the MixesDB admins and record the answer in CONTRIBUTING.md; if they decline, make `DJLIB_MIXESDB=off` (new in this release) the default.
- **`paste` from a real clipboard.** Reviews used a `pbpaste` stand-in and stdin; copying from 1001Tracklists in a browser and running `djlib set paste` hasn't been tried end to end.
- **The 0.1.0a15 → 0.1.0a16 upgrade on a real install**, including the traceback 0.1.0a15's upgrade may print after it installs.
- **rekordbox and USB with this build**: no push or USB export was run. Playback on a CDJ/XDJ is still not verified by djlib at all.
- **Installs on Linux and Windows** (CI runs the test suite there, but nobody installed this build on them), AudD and ACRCloud against their real services, and a set longer than two hours.
- **Review page time links** appear when djlib knows the set's link. Sets checked with an earlier 0.1.0a16 build, or saved through `djlib_create_request` without `source`, link only the times of IDs that carry it until `djlib set` checks them again.

## 0.1.0a15

Adds set identification (`djlib identify LINK`, `djlib_identify_set`, and `djlib set LINK` listening when a set's tracklist is missing or has IDs), automatic MixesDB tracklists, and Shazam history (`djlib set shazam[:new|DATE|apple-music|PATH]`, `djlib shazam status|list`, `djlib_shazam_history`), with song recognition installed by default on Python 3.12. See the [changelog](../CHANGELOG.md#010a15).

Live on 2026-10-13, from a wheel installed the way the installer does (`uv tool install --python 3.12 'dj-library-tool[download,recognize] @ …'`) into a scratch workspace with its own background service:
- **Keinemusik radio show (YouTube, 62 minutes, tracklist with times in the description).** 13 of 13 rows, every named song right: 8 confirmed, 3 likely (in the description, not heard), the opening ID unidentified, and the ID at 48:30 weak with Chris Fortier & Neil Kolo – All I Got as its first candidate. Confirmed songs start within 24 s of the answer key's times (12.8 s on average). 157 windows every 30 s took 7:23. With windows every minute (the spacing a timed tracklist now gets), 93 windows gave the same 13 rows and counts, but took 32 minutes: Shazam kept asking djlib to slow down after a day of testing.
- **Kaytranada at Boiler Room Montreal (SoundCloud, 42 minutes, no tracklist in the description).** MixesDB's page was found automatically (by title and length) and named in the result. 22 of 22 rows, every label the answer key's or Shazam's spelling. 115 windows every 30 s took 25:42 under almost continuous rate limiting (12 confirmed, 10 likely); 63 windows every minute took 6:52 (9 confirmed, 13 likely). Audio alone found 13 of the 22 songs.
- **`djlib set LINK`** reused both identifications (3.8 s and 14 s, most of it reading the page again), with each request item's time, confidence and evidence.
- **MCP** (`djlib mcp serve` over stdio): 22 tools; `djlib_identify_set` reused the Keinemusik result in 2 s; `djlib_shazam_history` without history answers `SHAZAM_HISTORY_NOT_FOUND` with the iCloud steps and alternatives.
- **Stopping.** Ctrl-C and `djlib jobs control JOB cancel` during a listen keep what was heard; `--again` kept the earlier tracklist until a new one was made.
- **Shazam history** without any history on the Mac: `djlib set shazam` and `djlib shazam status` explain the iCloud steps; a 3-row Shazam CSV became a set.

Before release, four independent reviews (runtime, output, security, accuracy) reproduced 48 findings against fakes, a real coordinator and the recorded live listens; the high and medium ones are fixed (see the changelog), with tests. Not verified: AudD and ACRCloud against their real services, a set longer than two hours, Shazam history on a Mac that has it (the test Mac has none), and how Shazam's limits behave for one set a day rather than dozens.

Local validation: the full suite passes on macOS with five Windows-only skips; Ruff is clean.

## 0.1.0a14

Fixes from a second end-to-end plugin test on the user's Mac (rekordbox 7.2.19, stick RICARDO_AM), whose USB step timed out three times, imported a duplicate “Name (3)” playlist and then stopped on `APP_MENU_DISABLED`; `--fetch` chose the official Phoenix – Lasso upload, which had become DRM-only, and did not fall back; and the assistant's MCP tools broke after an engine update. Root causes and fixes are in the [changelog](../CHANGELOG.md#010a14). Live on 2026-10-07 with the 0.1.0a14 code:
- **USB export.** `rekordbox usb` for the 5-track crate reused its existing playlist “Tonight — Plugin test (3)” (no new import), brought rekordbox to the front, reported why it was waiting (`no_playlist_selected`, `rekordbox_not_in_front` while the user switched apps), waited through one menu hiccup (`busy`) that would have ended an a13 run, and after the user's click found 5/5 entries on the stick in order and byte for byte, in 69 s including the wait (export 10.3 s).
- **Fetch, isolated workspace.** `set --fetch --yes --no-rekordbox` for “Phoenix - Lasso (Original Mix)” dropped the DRM-only official upload, chose Glassnote Records' upload (score 0.788, confident), downloaded it as MP3 (V0, about 239 kbps, 167.9 s) and made the song owned with no metadata review, in 11.2 s. A download request for the dead official URL with Glassnote as `alternates` succeeded through the alternate (`tried_urls` lists both). No empty download crates.
- **Accessibility probe.** rekordbox 7.2.19 exposes about 120 accessibility elements (player and top-bar controls); its playlist tree and track list are not exposed, so the one click for USB stays. The window's “N Tracks, M minutes, X MB” text reflects the selected playlist.
- **Install.** The new installer, run in a sandbox (separate HOME, uv tool dir, Claude Code and Codex config) with the a13 release, installed djlib, added the Claude Code plugin non-interactively (`claude plugin marketplace add` + `install`, about 3 s) and created the workspace for the music folder given in `DJLIB_MUSIC`, but its scan step was refused: `djlib scan` demanded the hidden `--key` from non-terminal callers (fixed here). The plugin's SessionStart hook is listed by `claude plugin details` and passes `claude plugin validate --strict`. Codex 0.161 adds the plugin with `codex plugin marketplace add` + `codex plugin add`.

Not verified: playback on a CDJ/XDJ, the installer's interactive music prompt on a real first install, and Windows. Local validation: see the release notes.

## 0.1.0a13

Fixes from an end-to-end run of the published 0.1.0a12 plugin by a separate Claude Code session on the user's Mac (9 tests: 5 passed, 2 partial, 2 blocked because the Mac was locked). That run showed: a locked Mac cost a 10-minute silent wait and a skipped rekordbox step read as success; `set --fetch` missed the official Phoenix – Lasso upload that `find_sources` found (the query kept “(Original Mix)”, live-verified fixed: both now pick soundcloud.com/phoenix/lasso, score 0.877, confident); re-runs bumped revisions without changes; the no-tracklist error carried listener comments only as prose; `crates` and `status` disagreed about rekordbox/USB state. Each is fixed and covered by tests. Not re-run yet: the blocked rekordbox import and USB steps of that plugin test. Local validation: 854 tests passed with five Windows-only skips.

## 0.1.0a12

A UX and product pass driven by three audits (a sandboxed first-time install, the CLI surface and output at 80/120 columns, and use from Claude Code/Codex). Measured on 2026-10-06:
- **Speed.** `djlib version` 0.85 s → 0.20 s (the CLI no longer imports server code; every command saves about 0.4 s); a 13-track `set --fetch` plan 31 s → 11.5 s (four searches at a time).
- **Assistants.** MCP `tools/list` 66,135 → 27,537 characters (19 core tools by default; 43 with `DJLIB_MCP_TOOLS=full`); the server starts in 1.3 s with no workspace and answers with the `djlib init` command; SKILL.md 3,013 → about 1,120 words. The plugin manifest passes `claude plugin validate --strict`, and Codex 0.160 reads it (MCP server and skill listed) in an isolated `CODEX_HOME`; neither host's real install was exercised.
- **First run** (clean sandbox home): `doctor` before `init` shows a "not set up yet" row with the command; `init` → `scan` → `set FILE --no-rekordbox` reported 3 of 5 owned, matching “(Original Mix)” to a bare-titled file and “Max Dean, Luke Dean, Jamie Jones” to reordered tags, kept `ID - ID @ 41:20` as an unknown with its time, and named the set from its heading; next steps print 8-character handles that the commands accept. The one-line installer installed the newest release with bytecode in a sandbox.
- **Reruns.** An unchanged `set` reuses its crate and playlist; a set that gained a song gets a new crate imported as “Name (2)” (tests with a simulated rekordbox).

Not verified here: a real `claude plugin install` / `codex plugin add` from GitHub (needs this on the default branch), Windows/Linux installs of the new installer, and a 100,000-file scan end to end (discovery of 50,000 files measured at about 19 s and 175 MB with ingestion stubbed). Local validation: 849 tests passed with five Windows-only skips.

## 0.1.0a11

Adds sets from a YouTube or SoundCloud link (tracklist from the description or chapters, listener comments as hints for IDs), `--fetch` for missing songs (YouTube/SoundCloud search, ranked; clear matches downloaded as MP3 after confirmation, the library copy tagged with the requested labels), MP3 downloads, artist country tags such as “(BR)” ignored in matching, and 1001Tracklists through the browser (its tracklist pages answer scripts with a captcha, which djlib does not get around).

Live on 2026-10-06:
- **Search and ranking.** For 7 well-known songs searched on both sites, the official upload ranked first in 6 and was marked clear in 5; 30-second SoundCloud previews, live recordings, extended edits and unrequested remixes were dropped or ranked down. Both searches together take about 1.5 s.
- **Set from a link.** A 62-minute YouTube radio show's description gave 13 tracks (2 IDs) in 31 s; for its 11 named songs, 5 had clear matches (3 before the country-tag fix), 2 are not on either site and 4 have competing edits, left for the user to pick.
- **Fetch end to end** (isolated workspace): a 3-line tracklist with one owned song downloaded the other 2 from YouTube as MP3 (about 270 kbps VBR), built the 3-track crate in order and imported it into rekordbox 7.2.19, in 44 s. A refused YouTube stream (seen once) succeeds when retried, which `--fetch` now does once; the managed MP3 copy carries the requested artist/title tags.
- **Comments.** On a Boiler Room upload with no tracklist, 300 YouTube comments yield the fan's timestamped tracklist (8 titles) and one credited remix, with no reactions or sentences; on a 2-hour SoundCloud mix, 67 timed comments named nothing usable and none were invented.

Not verified: download quality beyond the provider's stream (web audio is labelled unverified), and comment hints against a set whose IDs are known. Local validation: 752 tests passed with five Windows-only skips.

## 0.1.0a10

Adds `djlib set` (tracklist → owned/missing → crate → rekordbox playlist → USB in one command) and `rekordbox usb` (a crate goes to a USB stick through rekordbox's own Playlist > Export Playlist, then djlib reads the stick back), plus artist-order-insensitive request matching, key import from the stick, `doctor` rekordbox checks and a fresh-user pass.

Live on 2026-10-05 with rekordbox 7.2.19 and a FAT32 stick that already held a rekordbox library:
- **USB export.** The user clicked "Set 0" once; djlib confirmed the selection from rekordbox's export dialog, started the export, and all 28 tracks (24-bit FLAC) were on the stick after about 90 s. The first run reported early (8/28) because rekordbox shows export progress inside its window; completion is now read from the stick, and that wait, run against the same export, confirmed 28/28.
- **Device library.** Reading the stick's `export.pdb` back, "Set 0" lists 28 entries in crate order, each pointing at a file with the original's SHA-256. Across all 318 comparable tracks on the stick, its BPM matched rekordbox's XML export 318/318 and key 317/318.
- **`set` on the real library.** An 8-line test tracklist matched 5 owned songs exactly (including "Avalon Emerson & Moby" against tags "Avalon Emerson, Moby"), reported "Lasso (Original Mix)" as missing with the owned Two Door Cinema Club Remix as another version, kept one missing song and one unknown ID, and imported the crate into rekordbox as a playlist. Its USB step waited for a click that did not happen (the user was away) and exported nothing, as designed.
- **After release (2026-10-06), with the published 0.1.0a10 wheel.** `djlib set` with `--usb` on that tracklist reused the list, crate and playlist, waited for the click and confirmed 5/5 entries in the player's library, in order and byte for byte, 81 s from start including the click. Key/BPM read back from the stick matched all 5 catalog originals and agreed with rekordbox's earlier XML values (5 unchanged). A second run where the user first clicked "Set 0" printed “That's ‘Set 0’” and kept waiting; on the right click rekordbox re-exported in 12.6 s, replacing the playlist on the stick rather than duplicating it (325 tracks, one playlist of that name), and the check passed again.
- **Fresh install.** A clean wheel install with an isolated home walked through init, scan, status, requests, crates, doctor, setup-agent and MCP (42 tools) on six real tracks; the rough edges found were fixed in this release.

Not verified: playback on a CDJ/XDJ, and a stick without an existing rekordbox library. Local validation: 674 tests passed with five Windows-only skips.

## 0.1.0a9

Adds `djlib status` (library, BPM/key coverage, request lists, crates and whether each is already a rekordbox playlist, next commands) and a remembered default workspace (`djlib use`), plus `rekordbox pull --when-idle`. Checked against the user's real 690-recording workspace: request lists, crates and rekordbox playlist presence matched. Local validation: 626 tests passed with five Windows-only skips. USB export remains a native rekordbox step: its browser is not exposed to macOS accessibility, so djlib does not script it.

## 0.1.0a8

Moves work off the user's screen: rekordbox analysis is read from its analysis files in the background (BPM/cues; key still via a brief XML export), `rekordbox push` imports once per crate and can wait for idle time, scans decode each file once and skip known bytes, untagged files take labels from their names, and implicitly started coordinators exit when idle. Measured on the same 708-track real library: first scan 661 s → 189 s, rescan 15 s; analysis-file BPM matched rekordbox's XML for 959 of 959 comparable tracks; a request list became a rekordbox playlist in 9.5 s (8.8 s with rekordbox in front). Local validation: 622 tests passed with five Windows-only skips.

## 0.1.0a7

Adds the tracklist → owned/missing → crate workflow (`requests create --text`, `requests collect`), rekordbox BPM/key import from a Collection XML export, flag-based `delivery plan`, the terminal experience (readable views, live progress, guided `delivery observe`), the local review page (`djlib ui`) and catalog fixes from a hands-on audit. MCP grows to 42 tools. Local validation: 606 tests passed with five Windows-only skips; the installed-wheel smoke check exposed all 42 tools. Release-matrix and public-installation evidence are recorded on the GitHub release. Real-music check on 2026-10-05: 708 owned tracks (mostly 24-bit FLAC) indexed in 11 minutes with no failures; 280 tracks received BPM/key from an existing rekordbox 7.2.19 export; `rekordbox push` created two playlists in the user's rekordbox 7.2.19 (28/28 and 55/55 tracks, 55/55 analyzed), verified from rekordbox's own XML export. Serato, USB export and hardware playback remain unverified; see the a6 limits below, which still apply.

Updated 2026-10-05 UTC for **0.1.0a6**. The [a6 public release](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a6) was published at **07:14:53 UTC**. Its six-job release matrix, anonymous public installation and installed a3-to-a6 demo-copy compatibility checks passed. a4/a5 tags are preserved, unpublished candidates with failed release checks. The [a3 public release](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a3) remains a separate audited baseline. [Coverage](COVERAGE.md) separates implementation, observed evidence and remaining goals.

## a6 engineering changes

- Coordinator startup uses a 30-second authenticated-readiness window and 45-second startup lock, with distinct busy/failed/timeout recovery. This replaces the former 12-second window and improves failure diagnostics; the earlier Windows startup failures' underlying cause remains unproven.

- Windows session launchers start the coordinator before launching the AI host. Manual MCP configuration requires `djlib --workspace PATH service start` from an external terminal. Windows MCP cold start returns `COORDINATOR_START_REQUIRED`; no Windows Job Object escape is attempted.
- The 40-tool catalog/delivery API retains the following a4-candidate features. They are alpha capabilities, not completion of unattended personal-library management.

- Forty MCP tools, including catalog/saved-work paging, additive allowed-root management and explicit changed-file reconciliation; CLI validation identifies invalid fields without echoing private values.
- New scans use provisional byte identity for incomplete labels and preserve symbol-only distinctions. Historical incorrect merges are not repaired automatically.
- Durable `tag_only` and `replace_audio` reconciliation pins the old revision and current new hash, retains old memberships/annotations, and invalidates affected downstream evidence. Legacy tag-only work needs a verified decoded baseline or another original-byte location.
- Organization, passed analysis/native-export observations and app verification are queued jobs with item outcomes. Identical native-check intents return the same job. Completion returns an evidence-commit receipt; delivery status retains the check time and operator-conditional requirements, not fresh readiness.
- Full local preparation can proceed without a physical pilot, explicitly unvalidated. A supplied pilot must still match and pass. Native/device/playback gates remain; pilot/full working copies do not share native cues automatically.
- Read-only, bounded Windows partition-style detection preserves known GUID/filesystem evidence on failure. Automatic schema upgrades first create integrity-checked catalog backups; music/native databases are outside that backup scope.

## a6 release and installation evidence

Tag `282f92f9d75b36e7813ad3a9d93d215efbe7eacd` passed all six Linux/macOS/Windows and Python 3.12/3.13 jobs plus artifact packaging in the [release matrix](https://github.com/uneasymusings/dj-library-tool/actions/runs/37275004527). Both Windows jobs passed **521 tests with two skips**. Local source validation passed **518 tests in 60.81 seconds**, with five Windows-only skips; Ruff passed **96 files**, and 71 focused delivery tests passed after the fidelity-wording correction.

The anonymous public audit verified exactly four assets, checksums, complete wheel/ZIP skill equality and installed package bytes matching the wheel. The public source archive's smoke script exposed all **40 tool schemas** and passed actual stdio MCP calls, **three original tones**, request/organization checks and skill setup outside the checkout. A separate persistent demo/session passed; doctor found FFmpeg/ffprobe, yt-dlp and supported Node. The coordinator stopped with `url: null`. An installed a3-to-a6 trial on a separate demo-library copy preserved all catalog tables, identities, collection order, a nonempty annotation, media/export bytes and configuration/roots. This was binary compatibility at the same schema head, not a new schema migration. [Audit details and checksums](COVERAGE.md#independent-public-a6-distribution-audit).

An earlier Windows PR run failed a single one-second discovery-only check after forced-restart recovery. The cause remains unproven. Diagnostic-only commit `5b9004f` subsequently passed all six [push](https://github.com/uneasymusings/dj-library-tool/actions/runs/37275766324) and [PR](https://github.com/uneasymusings/dj-library-tool/actions/runs/37275770541) jobs; production code and packaged skill resources match the release tag. These passes do not establish a causal fix. [Validation details](VALIDATION.md#a6-public-release-validation) preserve the failure separately from the earlier startup failures. No new native app/device success or whole-library completion is claimed.

## Historical a5 candidate — unpublished

The local macOS/Python 3.13 suite passed **513 tests in 64.67 seconds**, with five Windows-only skips; Ruff passed **96 files**. Local integration checks passed six tests with the Windows-native case skipped. An installed a5 wheel passed **40-tool MCP / three-tone** smoke checks, requests, organization and session setup. Windows release checks subsequently failed on an incorrect SDK `is_error` assertion and the coordinator not becoming ready within the 12-second readiness window. The startup delays' underlying cause was not established. The a5 tag remains unchanged and unpublished; these checks are historical, not a6 proof.

## Historical a4 candidate checks — unpublished

| Check | Observed result |
| --- | --- |
| Local suite | **498 passed in 60.88 seconds**, macOS/Python 3.13; four Windows-only tests skipped. |
| Lint and formatting | Ruff checks passed for **95 files**. |
| Installed wheel | Smoke check passed actual stdio MCP calls with **40 tools** and **three original tones**. |
| Production-change matrix | [All six OS/Python jobs passed](https://github.com/uneasymusings/dj-library-tool/actions/runs/37269882474) for commit `810b95f`. A later Windows cold-start check confirmed the MCP SDK Job Object could terminate its child coordinator; the release candidate was cancelled. |
| Publication and public-artifact audit | Tag `039f1ca` remains unchanged and unpublished. No public a4 artifact audit passed. |
| Installed a3-to-a4 upgrade | A separate original-tone catalog copy retained all IDs, hashes, collection order and roots; the original workspace was unchanged. No schema change was needed. |
| Native app/device trial | Both three-tone app preparations passed; the UI attempt was blocked by disabled import controls/`noWindowsAvailable`. No new successful native import, analysis, loading, USB export or player validation. [Details](VALIDATION.md). |

These original-tone, upgrade and UI attempts belong to the a4 candidate, not the current release. The a3/a2 evidence below is retained independently. Neither engine checks nor an attempted UI import prove native musical analysis, real USB export or playback.

## Implemented first milestone

- JSON CLI, structured MCP tools, portable skill, and a separate project session generator for Codex/Claude Code. The skill ships in the wheel.
- Explicit workspace/source roots, authenticated localhost coordinator, SQLite/Alembic catalog, detached jobs, idempotency, generation fences, review revisions, and restart recovery.
- Local indexing, explicit collections, managed byte reuse, bounded publisher metadata inspection, selected public-source downloads.
- Full audio decoding/hashes, original acquisition provenance, chosen display tags on managed FLAC download copies, retained incoming originals.
- Hash-checked manifest/M3U/experimental rekordbox XML and read-only mount/capacity preflight.
- Runtime/lint/format/build CI for Linux/macOS/Windows with Python 3.12/3.13.

## Target-aware DJ delivery

- Persisted delivery requests freeze collection membership and selected recording identities; pilot/full phases separate small target trials from broad preparation.
- `rekordbox_import` and `serato_import` prepare native app libraries without requiring a player/controller model or USB. Their sourced input profiles describe an explicitly conservative subset; Serato numeric bounds are engine policy, not claimed vendor maxima. App verification commits saved requirements/readback evidence; queued completion returns only a receipt, never `ready_for_app_use` or USB departure readiness.
- Durable preparation creates isolated, labeled app working copies and playlist/manifest artifacts. Preserve is the default; compatibility conversion is explicit. Catalog originals remain unchanged.
- Four sourced hardware profiles distinguish CDJ-2000NXS, CDJ-3000, XDJ-RX3 and OPUS-QUAD audio/library requirements. Unknown metadata and unknown targets cannot silently pass audio checks. Firmware and physical hardware remain unverified.
- Typed, revision-checked observations record native import, analysis, export, device-library inspection and playback as operator reports. They are not automatic native app integration.
- Passing rekordbox hardware-playback reports require the matching player profile, actual firmware version and successful storage recognition. CDJ-3000 firmware 3.30 is rejected, including `v3.30`/`V3.30` spellings.
- Read-only device verification checks volume identity and expected post-analysis audio hashes. Database filenames are existence markers only; their contents, membership and analysis are not parsed.
- Full local preparation may omit a pilot ID, with explicit unvalidated status. Supplied pilot claims remain checked. App-only workflows use completed `verify-app` jobs after reported native import/analysis. Fresh `verify-device` evidence remains required for the departure flag; status and saved job reads do not rehash audio.
- Passed analysis/native-export observations and app verification use durable per-track jobs. Native-export checks require exact analyzed bytes and validate the bound volume at acceptance/finalization. Unchanged hashes avoid redundant decoding; changed working copies require stream/audio checks before a generation-fenced evidence commit. Native app behavior is still operator-reported.
- Native rekordbox Collection XML inspection compares prepared paths, playlist membership/order and declared app version read-only. It reports raw BPM/key and unknowns without granting readiness or automatically changing annotations. No native database or referenced media is opened.

See [DJ delivery](DJ_DELIVERY.md) for the four workflows, commands, exact evidence boundaries and organization suggestions. **No new physical-player or complete native USB export validation is claimed by these additions.**

## Exact requests and missing reports

The persisted request ledger accepts named artist/title/version requests and unknown IDs with source or timestamp evidence. It preserves missing, ambiguous and unavailable outcomes; source selection alone is not acquisition. Matching is exact after label normalization, with explicit resolution for multiple byte revisions. Bounded create/refresh/resolution checks inspect current files; reads return saved evidence and per-item refresh times. Larger lists can refresh selected items. Missing reports are written atomically within the workspace. This is a tested song-list reconciliation slice, not complete artist enumeration, automatic set identification, online search or an upgrade policy.

## Explicit DJ organization

Hash-checked metadata reads expose embedded BPM/key/genre/comments without inferring them. Byte-bound, revision-checked annotations store supplied notes, categories, role, energy and BPM/key provenance; embedded values and new BPM/key annotations default to unverified. Ordered collections use explicit recording/revision references, filters with unknown policies, deterministic sorting and per-item exclusion reasons. Original tags stay unchanged and recordings may appear in several collections. Delivery plans freeze annotations and apply supplied BPM/key/genre and descriptive comments to separate app working copies, retaining provenance in the manifest. MP4 fractional tempo stays exact in a freeform tag/manifest, with a native-display warning. This does not read native analysis databases, establish musical accuracy, translate harmonic key systems or set cues. See the [examples](ADVANCED.md#keep-notes-and-sort-explicit-selections).

## Evidence and precise limits

The following table retains published **a3 and a2 baseline evidence**. Historical host/provider/device observations were not rerun merely by implementing new code; unpublished a4-candidate checks are listed separately above.

| Area | Observed evidence | Remaining limit |
| --- | --- | --- |
| a3 local runtime | **403 tests passed in 45.51 seconds** on macOS/Python 3.13 with FFmpeg; three Windows-native tests skipped on macOS. Ruff lint/format checks passed for 79 source/script/test files. Published a2 baseline: **115 tests passed**, including a 200-item batch and recovery, integrity, CLI/MCP checks. | No multi-hour or 10,000-track performance claim. |
| Audio | Real WAV, FLAC, MP3, M4A, and AIFF decoding locally. Managed FLAC labels and original/final hashes checked. | Lossless output does not prove lossless source fidelity; no acoustic identity. |
| MCP | All 16 tools exercised through real MCP/ASGI/coordinator contracts; actual stdio subprocess reconnects and continues accepted work. | CI uses source fixtures, not live accounts. |
| Codex | Fresh **0.160.0** ephemeral sessions discovered the project skill, used MCP, completed two-tone collections/exports, and accurately reported `not_exported`; the latest trial used only the public GitHub-installed package outside the checkout. | Controlled end-to-end behavior trials; no broad model reliability claim. |
| Claude Code | Fresh **2.1.288** session completed a two-tone collection/export; second trial loaded `/dj-library`, checked items/reviews/collection, and correctly rejected an ordinary folder as USB evidence. | Project settings must remain enabled for skill discovery; disabling all setting sources suppressed it in the first trial. |
| Live sources | Bandcamp extractor fixture, SoundCloud publisher-declared CC0 track, and Blender's YouTube open movie each inspected, downloaded, decoded, tagged into managed FLAC, and cataloged successfully. | Three individual successes do not establish universal provider compatibility or DJ source quality. Removed YouTube fixture failed. |
| Dependencies | Locked source environment plus optional yt-dlp. FFmpeg/ffprobe and Node 22.15.0 present. Deno absent; Node fallback enabled. | Runtime availability does not guarantee a provider will accept extraction. |
| a2 packaging baseline | [v0.1.0a2 GitHub release](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a2) published; anonymous artifact downloads/checksums and a fresh public-URL tool installation outside the checkout passed. Installed-wheel smoke check passed demo/migration/export, bundled skill setup, and actual stdio with all 16 tool schemas. | There is no PyPI release. |
| a2 cross-platform | The a2 release commit passed runtime, lint, formatting, build, installed-wheel, and skill/checksum packaging on [all six OS/Python combinations](https://github.com/uneasymusings/dj-library-tool/actions/runs/37155504190). | FFmpeg-dependent cases could skip at that revision; native app evidence is macOS only. |
| a3 cross-platform release | The [tag release run](https://github.com/uneasymusings/dj-library-tool/actions/runs/37261484677) passed all six Linux/macOS/Windows and Python 3.12/3.13 jobs, including packaging and installed-wheel checks. Windows reported **404 passed, two POSIX-only skips**. The [first candidate](https://github.com/uneasymusings/dj-library-tool/actions/runs/37259264614) had failed both Windows jobs before the portability corrections. | Runner tests do not establish physical FAT32/exFAT USB compatibility or native app/player behavior. |
| rekordbox | **7.2.8**: generated M3U8 imported three original WAV tones; native waveforms/key analysis appeared in Collection. | Native XML import, playlists with real music, USB export, and player playback pending. |
| Serato | **DJ Pro 3.1.5** launched and its Files/library interface was visible. | Import/analysis not verified: the computer-use service timed out, then screen capture failed. No direct crate/database write performed. |
| USB release baseline | Read-only preflight tested for missing paths, folders, mount reporting, and capacity. No external physical disk was connected during the original app check. | This historical preflight evidence does not validate a native device library or hardware playback. The a3 readback workflow has controlled test evidence. |
| a3 delivery/catalog/XML | App/player profiles, working-copy conversion and annotation tags, request coverage, organization, native XML parsing, simulated evidence/readback, concurrency, coordinator version checks and transports passed within the local suite and release matrix. Migration from a2 passed. | Hash presence and database markers do not establish playable native playlists. |
| a3 public installation | Anonymous downloads confirmed exactly four release assets and all package checksums. A persistent public-wheel installation ran from isolated site-packages with bytes matching that wheel. The public source archive's smoke script passed three original tones, all 34 MCP schemas, request/organization calls and skill setup. A separate persistent demo/session passed; doctor found FFmpeg/ffprobe, yt-dlp and supported Node. Its coordinator stopped with `url: null`. | No developer checkout was used by the installed engine. This audit did not rerun signed-in AI-host trials, live music acquisition, native app import/analysis, or USB/player checks. |
| a3 native trial | The supported rekordbox UI imported the candidate's three-tone M3U8. A subsequent **native Collection XML export declares rekordbox 7.2.19** and contains all three exact prepared paths and matching playlist IDs/order. | The app bundle's older 7.2.8 metadata differs from the runtime snapshot; the trial declared that older version and cannot pass its version check. Track loading and musical BPM/key/grid accuracy remain unverified. Serato 3.1.5 capture failed, so import/analysis remains unverified. No USB readiness or passing analysis observation is claimed. |

The native rekordbox validation added only the three generated test tones through its own import UI. This is app import evidence, not an automatic native integration feature. Existing music was not batch imported or retagged. No device was formatted or written.

After the release tag was created, test-only commit `16d8461` improved Windows failure diagnostics and bounded scheduling checks; both its [push](https://github.com/uneasymusings/dj-library-tool/actions/runs/37262032083) and [PR](https://github.com/uneasymusings/dj-library-tool/actions/runs/37262035841) matrices passed all six jobs before publication. Production code and packaged resources are unchanged from release tag `05a8fff`. Two errors in an earlier PR run did not recur, but their root cause was not established; diagnostic hardening is not evidence of a causal fix.

## Next acceptance gate

1. Follow [fresh session setup](AGENTS.md) and start with original demo audio or a small owned selection.
2. Complete the [app-only native trials](VALIDATION.md) in both installed app versions: inspect membership/paths, analysis and loading, then perform fresh app verification. This scope needs no player model. Experimental XML import remains a separate unverified path.
3. Complete a [small target delivery](DJ_DELIVERY.md) using the actual app version, player/controller model and firmware, intended volume, native export/copy, device-library inspection, readback and physical playback. Record exact IDs/counts and retain any failed stages. Exercise rekordbox and Serato independently before claiming both workflows validated.
4. Exercise the user's chosen recordings; preserve provider failures and quality uncertainty.
5. Exercise native tag changes on isolated delivery working copies and confirm decoded-audio preservation and post-analysis hash readback. Changed original catalog hashes still require separate reconciliation.

Do not call the milestone ready for unattended personal-library use until the dependent app/device gate passes.

## Still planned

Soulseek via slskd; provider discovery/ranking and quality policies; artist catalog enumeration; full set timeline reconciliation (blends, repeats, edits) beyond what set identification names; upgrade policies and acquisition integration for the request ledger; genre/BPM/key/energy analysis; general native app reconciliation and automation; independently verified player-ready device export; standalone conversational CLI; an early-web public home; and browser parity with the CLI (the local review page, `djlib ui`, shows sets, crates and the library, and its only writes are picking a copy and building a crate).

See [PLAN.md](../PLAN.md) for full scope and milestone acceptance criteria.
