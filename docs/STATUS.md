# Implementation status

## 0.1.0a10

Adds `djlib set` (tracklist → owned/missing → crate → rekordbox playlist → USB in one command) and `rekordbox usb` (a crate goes to a USB stick through rekordbox's own Playlist > Export Playlist, then djlib reads the stick back), plus artist-order-insensitive request matching, key import from the stick, `doctor` rekordbox checks and a fresh-user pass.

Live on 2026-10-05 with rekordbox 7.2.19 and a FAT32 stick that already held a rekordbox library:
- **USB export.** The user clicked "Set 0" once; djlib confirmed the selection from rekordbox's export dialog, started the export, and all 28 tracks (24-bit FLAC) were on the stick after about 90 s. The first run reported early (8/28) because rekordbox shows export progress inside its window; completion is now read from the stick, and that wait, run against the same export, confirmed 28/28.
- **Device library.** Reading the stick's `export.pdb` back, "Set 0" lists 28 entries in crate order, each pointing at a file with the original's SHA-256. Across all 318 comparable tracks on the stick, its BPM matched rekordbox's XML export 318/318 and key 317/318.
- **`set` on the real library.** An 8-line test tracklist matched 5 owned songs exactly (including "Avalon Emerson & Moby" against tags "Avalon Emerson, Moby"), reported "Lasso (Original Mix)" as missing with the owned Two Door Cinema Club Remix as another version, kept one missing song and one unknown ID, and imported the crate into rekordbox as a playlist. Its USB step waited for a click that did not happen (the user was away) and exported nothing, as designed.
- **Fresh install.** A clean wheel install with an isolated home walked through init, scan, status, requests, crates, doctor, setup-agent and MCP (42 tools) on six real tracks; the rough edges found were fixed in this release.

Not verified: playback on a CDJ/XDJ, a stick without an existing rekordbox library, and key import from a real stick (covered by tests with a synthetic `export.pdb`). Local validation: 674 tests passed with five Windows-only skips.

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

Hash-checked metadata reads expose embedded BPM/key/genre/comments without inferring them. Byte-bound, revision-checked annotations store supplied notes, categories, role, energy and BPM/key provenance; embedded values and new BPM/key annotations default to unverified. Ordered collections use explicit recording/revision references, filters with unknown policies, deterministic sorting and per-item exclusion reasons. Original tags stay unchanged and recordings may appear in several collections. Delivery plans freeze annotations and apply supplied BPM/key/genre and descriptive comments to separate app working copies, retaining provenance in the manifest. MP4 fractional tempo stays exact in a freeform tag/manifest, with a native-display warning. This does not read native analysis databases, establish musical accuracy, translate harmonic key systems or set cues. See the [quickstart examples](QUICKSTART.md#keep-notes-and-sort-explicit-selections).

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

Soulseek via slskd; provider discovery/ranking and quality policies; artist catalog enumeration; full set timeline reconciliation/acoustic recognition; upgrade policies and acquisition integration for the request ledger; genre/BPM/key/energy analysis; general native app reconciliation and automation; independently verified player-ready device export; standalone conversational CLI; early-web public home and optional local browser UX.

See [PLAN.md](../PLAN.md) for full scope and milestone acceptance criteria. No frontend has started.
