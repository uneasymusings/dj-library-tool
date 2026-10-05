# Validation and first-use checklist

This document separates repeatable engine checks from actual host, provider, app and hardware observations. Updated 2026-10-05 UTC for **0.1.0a6**. Local source, a6 installed-wheel and six-job startup-fix branch checks passed; final a6 tag and public-artifact gates remain pending. Unpublished a4/a5 and published a3/a2 observations below are historical evidence, not new-version validation.

## a6 release gates — pending

Current local source validation passed **518 tests in 60.81 seconds**, macOS/Python 3.13, with five Windows-only skips. Ruff lint/format passed **96 files**. After clarifying that compatibility conversion can reduce fidelity, **71 focused delivery tests passed**. The [startup-fix branch matrix](https://github.com/uneasymusings/dj-library-tool/actions/runs/37273853767) passed all six OS/Python jobs at `0e7e586`, including the actual Windows SDK guard, same-instance coordinator lifetime and accepted-job recovery tests. Each Windows job passed 521 tests with two skips (Python 3.12: 364.06 seconds; Python 3.13: 373.93 seconds); installed-wheel checks exercised 40 tools and three original tones. A local a6 installed-wheel smoke also passed actual 40-tool stdio MCP, three original tones, request/organization and bundled-skill checks; `uv lock --check` passed with 64 packages. After the final documentation freeze, the resource build repeated the installed-wheel check successfully. The fidelity-wording commit also passed its [six-job branch matrix](https://github.com/uneasymusings/dj-library-tool/actions/runs/37273980856) at `73159f8`. Final a6 tag CI and anonymous public artifact/install checks remain pending. Do not substitute prior candidate outcomes for those gates.

The Windows checks cover external coordinator startup, MCP cold-start rejection with `COORDINATOR_START_REQUIRED`, same-instance continuation after SDK exit, and accepted-job recovery after forced coordinator termination. Startup now has a 30-second readiness window and 45-second lock, with separate busy/failed/timeout errors; the tests do not establish the cause of prior runner delays. Native app and hardware gates remain separate.

## Historical a5 candidate — unpublished

The a5 local suite passed **513 tests in 64.67 seconds**, with five Windows-only skips; Ruff passed **96 files**. Six integration tests passed locally with the Windows-native case skipped. An installed a5 wheel passed actual **40-tool MCP / three-tone** checks, skills and request/organization workflows, without native app observations. Subsequent Windows release checks exposed an incorrect SDK `is_error` assertion and the coordinator not becoming ready within the earlier 12-second window. The underlying startup-delay cause remains unproven. The candidate tag is immutable and unpublished; no anonymous public a5 installation was validated.

## Historical a4 candidate — cancelled and unpublished

The local macOS/Python 3.13 suite passed **498 tests in 60.88 seconds**, with four Windows-only tests skipped. Ruff lint/format checks passed for **95 files**. A fresh installed-wheel smoke check passed actual stdio calls for **40 MCP tools** and **three original tones**. These are local/installed-artifact results, not an anonymous public-download audit.

The [production-change matrix](https://github.com/uneasymusings/dj-library-tool/actions/runs/37269882474) passed all six Linux/macOS/Windows and Python 3.12/3.13 jobs for commit `810b95f`. A later Windows cold-start check confirmed that MCP SDK Job Object cleanup could terminate a coordinator spawned inside that job. The release was cancelled; tag `039f1ca` is preserved and unpublished, with no public artifact audit. These results do not establish a6 compatibility. Candidate regressions covered provisional/symbol identity, reconciliation/history, paging, roots, durable native checks, optional-pilot preparation and Windows partition metadata. The Windows-native partition test uses the runner's system volume, not a physical DJ USB/player.

An actual installed a3-to-a4 binary upgrade on a separate demo-library copy retained all three recording/revision IDs and hashes, the collection ID/revision and ordered membership, and unchanged configuration/roots. All twelve files in the original stopped workspace remained byte-identical. Both versions used schema revision `f12d20261005`, so this exercised binary upgrade compatibility rather than a schema migration. Both temporary coordinators stopped. Separate regression tests exercise checked backups before schema migrations.

A bounded native trial used the installed a4-candidate wheel with three original tones and a separate workspace/session. It declared rekordbox's bundle build **7.2.19.0342** and Serato DJ Pro **3.1.5**; the rekordbox value was not independently established through About/native XML and must not be treated as the confirmed runtime version. Both preparations completed three tracks without changing source hashes. Screenshots/accessibility worked, but coordinate actions returned `-10005 noWindowsAvailable`. Rekordbox's playlist chooser kept Open disabled for the M3U8 and an identical M3U copy; no import occurred. Serato's Files control could not be operated reliably. No import/analysis/loading, native XML, device export or playback success was recorded. The coordinator stopped with `url: null`; no external physical USB was detected. This is an unsuccessful supervised attempt, not an engine-format defect or a6/native integration proof.

Exercise a small owned-music app trial only after the user supplies actual allowed roots. Confirm membership, analysis, loading and saved-state preservation in each app. Native Serato, real USB export and player playback remain unverified; neither queued verification nor a passing engine test substitutes for those observations.

## Automated suite

```bash
uv sync --locked --extra download
uv run pytest -q
uv run ruff check src scripts tests
uv run ruff format --check src scripts tests
uv build
```

### Published a3 baseline — 2026-10-04/05

Version: **0.1.0a3**. After the Windows corrections, the local suite passed **403 tests in 45.51 seconds**, macOS, Python 3.13, real FFmpeg/ffprobe installed; only three Windows-native tests skipped on macOS. Ruff lint/format checks passed for 79 source/script/test files. The [release tag matrix](https://github.com/uneasymusings/dj-library-tool/actions/runs/37261484677) passed all six OS/Python jobs and its draft-artifact job. Windows reported **404 passed, two POSIX-only skips**. The [first candidate matrix](https://github.com/uneasymusings/dj-library-tool/actions/runs/37259264614) had exposed Windows failures before the portability corrections. Tests use original tones, isolated catalogs, controlled transports and simulated device/operator evidence; they do not establish native app, device export or playback success.

Follow-up commit `16d8461` changes test diagnostics and bounded scheduling checks only; production code and packaged resources match tag `05a8fff512f8b828897259b66614e0f42967c928`. Both its [push matrix](https://github.com/uneasymusings/dj-library-tool/actions/runs/37262032083) and [PR matrix](https://github.com/uneasymusings/dj-library-tool/actions/runs/37262035841) passed all six jobs before publication, and the focused local checks passed. Two errors from an earlier PR attempt did not recur; their root cause was not established, so this does not claim the diagnostic changes fixed those errors.

Coordinator upgrade checks reject old or unknown engine versions before sending ordinary requests. Authenticated capabilities, status and explicit shutdown remain usable; no old coordinator is replaced and no operation is resubmitted automatically. Twenty-seven focused cases plus actual process/stdio integration cover this boundary.

| Added suite | Scenarios |
| --- | --- |
| `test_worker_scheduling` | Late-arriving collection/export requests, serial download rotation, bounded handoff preference, checkpoint pause/resume, restart recovery, stale generations, and delivery-item/finalization dispatch. |
| `test_delivery_workflow` | Frozen stable IDs and collection membership, conflicting byte revisions, immutable originals, pilot gating, partial preparation, stage/coverage/revision evidence, manifest tampering, native tag changes versus replaced audio, invalidated stale readiness, and actual-player/firmware detail requirements. |
| `test_delivery_media` | Isolated compatibility copies, source integrity, output properties, supplied annotation tags/comments across five formats, and interruption/failure boundaries. |
| `test_delivery_targets` | Documented target-profile limits and rejection of unsupported or insufficiently specified audio properties. |
| Native rekordbox XML tests | Bounded descriptor reads/parsing, exact prepared paths and playlist membership/order, duplicate/unknown/version mismatches, and snapshot-only reports without readiness or referenced-media access. |
| `test_app_targets` | Separate app input subsets, conservative unknown/unsupported handling, filename/channel checks and no hardware-readiness implication. |
| `test_requests` | Exact version matching, byte-revision ambiguity, unknown IDs, bounded freshness checks, explicit resolution and missing reports. |
| `test_organization` | Five-format embedded metadata, byte-bound annotations, source preservation, concurrent revisions, stable ordered collections and unknown/exclusion policies. |
| `test_device_readback` | Read-only mount identity, native-marker limitations, bounded hash presence checks, traversal boundaries, and incomplete/changed-volume outcomes. |
| `test_delivery_transports` | Delivery commands/tools through real CLI/MCP/ASGI/worker contracts with original tones; device/native observations remain controlled test inputs. |
| `test_delivery_concurrency` | One preparation job under concurrent submissions and one winner for competing evidence updates at the same revision. |

Newly generated sessions use a 660-second Codex MCP tool timeout and a 660,000-millisecond Claude server timeout, covering the client's 600-second delivery-observation/app-verification timeout. Existing generated sessions are unchanged.

An earlier development wheel passed the separate 33-tool check below, and migration from a2 passed. The final 34-tool a3 wheel passed its own fresh isolated installation and actual stdio check, including request matching, unknown reports, annotations and filtered collections. The smoke script stopped its coordinator and performed no native app actions. No new signed-in AI-host trial, live provider acquisition or physical USB/player trial was part of a3 validation. Native rekordbox XML confirms prepared paths and playlist membership/order; loading and musical analysis accuracy remain unverified.

### Historical public-alpha baseline — 2026-10-03

For `0.1.0a2`, the recorded local result was **115 passed in 22.76 seconds**, macOS, Python 3.13.3, FFmpeg/ffprobe installed. The following table records that baseline; its 16-tool count is not the current a3 count. Tests generated their own short tones without accessing a personal music catalog or physical USB. Live network access was not a test-suite prerequisite.

| Suite | Scenarios |
| --- | --- |
| `test_contracts_workspace` | Strict fields, Unicode/version identity, private initialization, reinitialization, allowed roots/symlinks, atomic JSON. |
| `test_audio_export` | Full five-format decoding, corruption/empty payloads, missing dependencies, XML escaping/URIs/references, read-only capacity checks. |
| `test_application_worker` | Plan revisions, semantic keys, duplicate bytes/membership, metadata choices/stale reviews, partial retries, pause/resume/cancel, promoted-file recovery, fencing, review-resolution race, 200-item batch/cursors, managed FLAC labels/final hashes. |
| `test_sources` | URL bounds, malformed metadata/chapters/receipts, untrusted text limits, subprocess exits/timeouts/output/staging limits, error classification, bounded stderr, cancellation including POSIX descendants. |
| `test_http` | Token, browser origin, host checks, strict body/query validation, reads, plans/submissions/controls, source errors. |
| `test_integration` | Fresh CLI processes, singleton coordinator startup, actual stdio MCP reconnect, all 16 MCP tools with real HTTP/worker/catalog and controlled providers. |
| `test_release_artifacts` | Engine/project/lock version consistency, complete standalone skill allowlist, reproducible ZIP bytes, and checksums for current release artifacts only. |
| `test_agent_setup` | Both portable skills, absolute executable config, existing-output preservation, workspace boundaries, launcher option placement, clean MCP stdout on startup failure. |

The public `0.1.0a2` release passed its three-OS/Python 3.12–3.13 CI matrix. At that revision, five FFmpeg-dependent cases skipped if the runner had no decoder. The POSIX descendant test skipped on Windows; Windows retrieval used `taskkill /T` for process-tree cancellation. Symlink tests could skip where link creation was unavailable. These historical matrix results do not establish cross-platform results for the a3 suites; check each actual run's summaries.

## Historical AI CLI trials — 2026-10-03

Create a new isolated workspace, then a separate session:

```bash
uv run djlib --workspace /absolute/path/trial-library demo
uv run djlib --workspace /absolute/path/trial-library setup-agent --output /absolute/path/trial-session
cd /absolute/path/trial-session
python launch.py codex
# or:
python launch.py claude
```

Trial prompt:

> Use the dj-library skill. Read capabilities, find the three original demo tones, and build a named collection using their exact labels/paths. Keep one stable submission key. Poll to completion, check items and reviews, export the collection, and report actual artifact paths plus the remaining native app/USB steps.

For scripted Codex checks, `python launch.py codex exec --ephemeral --skip-git-repo-check ...` uses the installed host's `--ignore-user-config` option in the correct subcommand position. Interactive Codex retains user settings. Both use explicit djlib MCP overrides. For Claude, the launcher supplies `--strict-mcp-config`; `--setting-sources project` preserves the installed project skill while omitting personal settings. Do not turn off all setting sources and then assume skill discovery still works.

Observed `0.1.0a2` trials:

- Codex 0.160.0 automatically read `.agents/skills/dj-library/SKILL.md`, read capabilities/catalog, planned/started a two-tone collection, read items/reviews/collection, and completed its export. Counts: two succeeded, both reused, zero reviews; export two tracks; USB `not_exported`.
- Claude Code 2.1.288 completed a two-tone collection/export. The initial restricted test omitted project setting sources and suppressed skill discovery; a corrected trial loaded `/dj-library` successfully, checked all item states and reviews, and performed read-only preflight on an ordinary folder with `is_mount_point=false`. No permission denials in the corrected trial.

Each trial used actual signed-in host inference and real MCP tools, without approval bypasses or delegation. These are controlled evaluations rather than a guarantee that every assistant prompt will be interpreted correctly. Transcripts/authentication details are not published.

## Historical live provider checks — 2026-10-03

The official yt-dlp Bandcamp extractor fixture, `https://youtube-dl.bandcamp.com/track/youtube-dl-test-song`, was inspected and acquired through the actual CLI/coordinator. Result: one succeeded; managed FLAC, about 9.848 seconds, mono 44.1 kHz; original acquisition hash and final tagged hash retained; `source_quality=unverified`, `acoustic_identity_verified=false`.

SoundCloud's [Testing Grounds (Creative Commons ZERO)](https://soundcloud.com/303bassline/testing-grounds), by Nicolás Díaz, passed inspection and download: one succeeded, with publisher-declared public-domain permission. YouTube's [Sintel open movie](https://youtu.be/eRsGyueVLvQ), published by Blender, also passed inspection and complete audio acquisition: one succeeded. The film is an extractor fixture, not a claim of a DJ-track identification. Both used the actual durable CLI/coordinator workflow.

The prior YouTube fixture `BaW_jenozKc` was unavailable. It failed rather than being reported as acquired. That trial used supported JavaScript runtime/EJS extraction with Node 22.15.0; consult [official setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS) when preparing another environment. Three individual provider successes do not guarantee extraction for arbitrary links; CI provider doubles do not establish live compatibility.

## Installed-package check

After building, run:

```bash
uv run --locked python scripts/check_distribution.py
uv run --locked python scripts/release_artifacts.py
```

Earlier on **2026-10-04**, a development `0.1.0a3.dev0` wheel passed in a separate installed environment before native XML inspection was added:

```json
{"ok": true, "tracks": 3, "mcp_tools": 33, "skill_packaged": true}
```

That earlier candidate ran from installed site-packages, ingested/exported three original tones and exposed 33 tool schemas through real stdio. The packaged skill installed into a separate session; MCP delivery targets/plan/prepare completed an app-only pilot with `prepared_for_import=true` and `ready_for_departure=false`. This verifies the earlier installed build, not the final 34-tool candidate, native automation or device readiness.

The earlier `0.1.0a2` installed-package check reported three tracks, 16 tools, and the packaged skill. The independent a2 public-installation audit remains in [coverage](COVERAGE.md); it does not make the new APIs part of a2. The separate a3 public-artifact audit below verifies the published 34-tool package.

The script uses a temporary workspace, stops its coordinator, and waits for its lock before cleanup. Windows cleanup additionally waits for the exiting process to close its inherited log handle. The installed-wheel smoke check passed in the six-job release matrix and separately against the public a3 wheel.

## Public a3 distribution check — 2026-10-05 UTC

The [v0.1.0a3 prerelease](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a3) was published at **04:15:02 UTC**, from tag commit [`05a8fff512f8b828897259b66614e0f42967c928`](https://github.com/uneasymusings/dj-library-tool/commit/05a8fff512f8b828897259b66614e0f42967c928). The ordinary published `uv tool install --python 3.13 'dj-library-tool[download] @ …v0.1.0a3…whl'` command installed into a new persistent tool environment outside the checkout, preserving existing installations and music.

- Anonymous downloads found exactly the wheel, source archive, standalone skill ZIP and `SHA256SUMS`; all three package hashes matched. Wheel and ZIP skill resources/license matched byte for byte.
- The module resolved inside that tool environment's site-packages; installed package bytes matched the checked public wheel. `djlib version` returned `0.1.0a3`.
- The smoke script came from the checksummed public source archive and ran with the installed interpreter, isolated imports and a working directory outside the checkout. It passed actual stdio MCP calls, **34 tool schemas**, **three original tones**, request matching/missing reports, annotations, organization and separate skill setup. App preparation used synthetic app-version evidence; no native app was operated.
- A separate persistent original-tone demo completed ingestion/export and generated a separate assistant session with both complete project skills. Doctor detected FFmpeg, ffprobe, yt-dlp and supported Node. The demo coordinator stopped, and a subsequent status returned `url: null`.

Artifact hashes and the historical a2 comparison are recorded in [coverage](COVERAGE.md). This is public installation and engine/MCP evidence on macOS/Python 3.13, not a new signed-in AI-host, live provider, native app or hardware trial. Private paths, catalog IDs and transcripts are omitted.

## Historical public GitHub distribution check — 2026-10-03

The [v0.1.0a2 prerelease](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a2) was installed directly from its public wheel URL into a fresh uv tool environment outside the developer checkout. The trial used no editable install or local wheel. Runtime dependencies resolved through their normal registry, including MCP 2.3.0 and yt-dlp 2026.8.19.

Observed checks:

- Installed engine reported `0.1.0a2`; its package and interpreter paths were inside the fresh tool environment, with no developer checkout dependency.
- Publicly fetched smoke-check script completed: three original tones ingested/exported, 16 real stdio MCP schemas, and the packaged skill installed successfully.
- A fresh signed-in Codex session read the installed project skill and used the publicly installed MCP engine to create/export a two-tone collection: two accepted, zero failures, zero reviews, two exported. Manifest readback confirmed `prepared_for_import` and `not_exported`. No personal music or device writes were used.
- Installed CLI created a new demo and assistant session; both hosts' skills included CLI and installation references. MCP configuration pointed to the persistent installed interpreter.
- `doctor` detected FFmpeg, ffprobe, the download extra, and Node in the local trial environment. Those native programs are separate prerequisites, not bundled artifacts.
- Wheel, source archive, skill ZIP, and checksum manifest downloaded without GitHub authentication; all three artifact hashes matched. The skill ZIP contained only its entrypoint, two references, and license.
- [Release commit CI](https://github.com/uneasymusings/dj-library-tool/actions/runs/37155504190) passed all six matrix jobs. An earlier trial exposed a shutdown race in integration-test cleanup; cleanup now waits for the coordinator lock after CLI shutdown instead of sending another request during process exit.

Follow [the public installation guide](INSTALL.md) for the same installation. Tool/cache directories were overridden only to isolate this trial from an existing personal installation. The installation still runs the engine locally for music access.

## Historical native rekordbox check — 2026-10-03

Verified in **rekordbox 7.2.8 on macOS**:

1. Generate the original-tone demo and read its export job's `playlist_path`.
2. File → Import → Import Playlist → choose `collection.m3u8`.
3. Wait for all three tracks to import. In Collection, search for the demo track names and press Return to apply the search.
4. Confirm three tracks and native analyzed waveforms/key display. The original first trial used filenames `tone-1`, `tone-2`, `tone-3`; newly generated demos include clear embedded artist/title tags.

The installed app had an existing personal catalog; the check added only the generated tones. No native database files were edited directly. Actual XML-library import and device export have not yet passed. The manifest correctly remains `prepared_for_import`/`not_exported`: engine state does not automatically observe native app actions.

Native apps may legitimately change file tags. Generic catalog exports still reject changed catalog bytes with `RECONCILIATION_REQUIRED`; do not disable that check. The a3 delivery workflow prepares separate working copies and checks decoded PCM identity when native analysis changes their tags. That behavior has generated-audio test evidence today, not a new native rekordbox observation.

## Current native trial — 2026-10-04

The supported Import Playlist UI selected the installed candidate's three-original-tone M3U8 and completed `Importing 3/3`. Subsequent coordinate actions returned `noWindowsAvailable`, but the supported **File > Export Collection in xml format** UI succeeded. This native snapshot contains all three exact prepared working paths and the matching playlist's three TrackIDs in the expected order. It declares **rekordbox 7.2.19**, differing from the old 7.2.8 app-bundle metadata used to declare this trial. That mismatch stays unresolved; do not fabricate matching-version observations. The native snapshot is private local validation data and is not shipped.

The snapshot reports zero/unknown BPM for the short tones and key labels, which are not musical accuracy evidence. Track loading, audition and BPM/key/grid correctness remain unverified. No passing analysis or app-readiness record was inferred from this export, and no native database was edited directly.

Serato DJ Pro **3.1.5** was running, but application capture failed; import and analysis remain unverified. Both apps need an app-only pilot with actual membership/analysis/load observations before `verify-app` can support readiness. No player model or USB is needed for this app-only gate. The short tones establish pipeline behavior only, not musical BPM/key/grid accuracy.

## Serato check to complete

In the **2026-10-03** trial, Serato DJ Pro **3.1.5** opened and exposed its library/Files interface. The automation service subsequently failed with screen-capture errors, so import and analysis were unverified. The 2026-10-04 capture attempt above did not resolve that missing evidence.

Manual procedure for the next supervised trial:

1. For the delivery workflow, prepare an original-tone pilot and locate its isolated working copies and manifest. Open those working copies in Serato Files.
2. Create a distinctly named test crate and add only those generated tracks using Serato's supported UI.
3. Confirm three entries and their paths, then run Analyze Files. Verify no missing-file warning and load one tone into a deck with audio output under your control.
4. Record the observed app version, count, analysis/load outcome, and any errors. An M3U file by itself is not a native Serato crate.

Selected managed FLAC downloads now contain readable artist/title/version display tags, with incoming originals retained. This has engine/live-download evidence; Serato display compatibility still needs the native check.

## Physical USB and player gate

The published a2 original-tone validation recorded no external physical USB disk. The a3 validation also did not test a physical USB or player. A mounted app installer image is not a target USB. The engine does not format, write to or create a native player database on a device.

Once the user supplies the exact mounted volume and player/controller model:

1. Use `delivery targets` and the actual workflow, app version, and player model to plan an original-tone pilot. Bind the exact mounted volume through `delivery bind-device`; ordinary folder capacity is not device identity.
2. Check the player's official supported filesystem/audio/export database formats and actual firmware. Preserve existing correctly prepared media; formatting is not a routine step.
3. Import/analyze the prepared working copies and perform the supported native rekordbox export or Serato portable-crate copy described in [the delivery workflow](DJ_DELIVERY.md).
4. Inspect actual native playlist/crate membership, run `delivery verify-device`, eject normally, and load/play every pilot track on the specified player or destination Serato setup. Record the actual hardware details required by the target workflow.
5. Record each native/device/hardware observation and perform fresh device verification before departure. `delivery get` reports historical evidence and lightweight current-device checks; it does not rehash the audio. A capacity check, M3U/XML generation, native-library filename, or file copy cannot substitute for native and hardware evidence.

After the small gate passes, try a small owned-music collection before scaling its delivery. Soulseek, complete discographies, automatic set recognition, inferred musical categorization, and unattended app/USB automation are subsequent milestones in [the plan](../PLAN.md). Explicit catalog annotation and filtering are narrower a3 capabilities, not those automatic analyses.
