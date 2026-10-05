# Implementation status

Updated 2026-10-05 UTC for **0.1.0a6**. Local source, a6 installed-wheel and six-job startup-fix branch checks passed; final a6 tag and public-artifact checks remain pending. a4/a5 tags are preserved, unpublished candidates with failed release checks. The [a3 public release](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a3) remains a separate audited baseline. [Coverage](COVERAGE.md) separates implementation, observed evidence and remaining goals.

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

## a6 release gates — pending

Current local source validation passed **518 tests in 60.81 seconds**, with five Windows-only skips; Ruff lint/format passed **96 files**. After correcting the source-fidelity wording, **71 delivery tests passed**. The [startup-fix branch matrix](https://github.com/uneasymusings/dj-library-tool/actions/runs/37273853767) passed all six OS/Python jobs for commit `0e7e586`, including the Windows SDK guard, coordinator lifetime and recovery checks. Both Windows jobs passed 521 tests with two skips; installed-wheel checks exercised 40 tools and three original tones. A local a6 wheel also passed actual 40-tool stdio MCP, three original tones, request/organization and bundled-skill checks; `uv lock --check` passed with 64 packages. The later fidelity-wording commit also passed its [six-job branch matrix](https://github.com/uneasymusings/dj-library-tool/actions/runs/37273980856) at `73159f8`. The final documentation/resource build repeated the installed-wheel check successfully. Final a6 tag CI and anonymous public installation remain pending. No a6 publication or new native app/device success is claimed.

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
