# Validation and first-use checklist

This document separates repeatable engine checks from actual host, provider, app, and hardware observations. Updated 2026-10-03; see [status](STATUS.md) for remaining scope.

## Automated suite

```bash
uv sync --locked --extra download
uv run pytest -q
uv run ruff check src scripts tests
uv run ruff format --check src scripts tests
uv build
```

Local result: **115 passed in 22.35 seconds**, macOS, Python 3.13.3, FFmpeg/ffprobe installed. Tests generate their own short tones and never access a personal music catalog or physical USB. Live network access is not a test-suite prerequisite.

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

CI runs the suite on three OSes and Python 3.12/3.13. Five FFmpeg-dependent cases skip if the runner has no decoder. The POSIX descendant test skips on Windows; Windows retrieval uses `taskkill /T` for process-tree cancellation. Symlink tests can skip where link creation is unavailable. Check the run's summaries instead of assuming every optional case ran on every platform.

## Fresh AI CLI trial

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

Observed trials:

- Codex 0.160.0 automatically read `.agents/skills/dj-library/SKILL.md`, read capabilities/catalog, planned/started a two-tone collection, read items/reviews/collection, and completed its export. Counts: two succeeded, both reused, zero reviews; export two tracks; USB `not_exported`.
- Claude Code 2.1.288 completed a two-tone collection/export. The initial restricted test omitted project setting sources and suppressed skill discovery; a corrected trial loaded `/dj-library` successfully, checked all item states and reviews, and performed read-only preflight on an ordinary folder with `is_mount_point=false`. No permission denials in the corrected trial.

Each trial used actual signed-in host inference and real MCP tools, without approval bypasses or delegation. These are controlled evaluations rather than a guarantee that every assistant prompt will be interpreted correctly. Transcripts/authentication details are not published.

## Live provider check

The official yt-dlp Bandcamp extractor fixture, `https://youtube-dl.bandcamp.com/track/youtube-dl-test-song`, was inspected and acquired through the actual CLI/coordinator. Result: one succeeded; managed FLAC, about 9.848 seconds, mono 44.1 kHz; original acquisition hash and final tagged hash retained; `source_quality=unverified`, `acoustic_identity_verified=false`.

SoundCloud's [Testing Grounds (Creative Commons ZERO)](https://soundcloud.com/303bassline/testing-grounds), by Nicolás Díaz, passed inspection and download: one succeeded, with publisher-declared public-domain permission. YouTube's [Sintel open movie](https://youtu.be/eRsGyueVLvQ), published by Blender, also passed inspection and complete audio acquisition: one succeeded. The film is an extractor fixture, not a claim of a DJ-track identification. Both used the actual durable CLI/coordinator workflow.

The prior YouTube fixture `BaW_jenozKc` is unavailable. It failed rather than being reported as acquired. Current YouTube extraction requires a supported JavaScript runtime/EJS; [official setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS) recommends Deno and supports Node 22+ when explicitly enabled. Node 22.15.0 was used for the successful trial. Three individual provider successes do not guarantee extraction for arbitrary links; CI provider doubles do not establish live compatibility.

## Installed-package check

After building, run:

```bash
uv run --locked python scripts/check_distribution.py
uv run --locked python scripts/release_artifacts.py
```

Observed result: `ok=true`, three generated tracks ingested/exported, 16 MCP tool schemas exposed through a real stdio child, and the packaged skill installed into a separate agent session. The script uses a temporary workspace, stops its coordinator, and waits for its lock before cleanup. Windows also waits for the exiting process to close its inherited log handle before removing the disposable workspace. CI runs this fresh-environment smoke check after building the wheel.

## Actual rekordbox check

Verified in **rekordbox 7.2.8 on macOS**:

1. Generate the original-tone demo and read its export job's `playlist_path`.
2. File → Import → Import Playlist → choose `collection.m3u8`.
3. Wait for all three tracks to import. In Collection, search for the demo track names and press Return to apply the search.
4. Confirm three tracks and native analyzed waveforms/key display. The original first trial used filenames `tone-1`, `tone-2`, `tone-3`; newly generated demos include clear embedded artist/title tags.

The installed app had an existing personal catalog; the check added only the generated tones. No native database files were edited directly. Actual XML-library import and device export have not yet passed. The manifest correctly remains `prepared_for_import`/`not_exported`: engine state does not automatically observe native app actions.

Native apps may legitimately change file tags. If later export says `RECONCILIATION_REQUIRED`, preserve the original and inspect the change; current byte revisions are immutable and automatic native-tag reconciliation is still planned. Do not disable hash checks to make a demo appear successful.

## Serato check to complete

Serato DJ Pro **3.1.5** opened and exposed its library/Files interface. The automation service subsequently failed with screen-capture errors, so import and analysis are unverified.

Manual procedure for the next supervised trial:

1. Open Files and locate the demo's `demo-source/` folder (or accepted managed `media/` files).
2. Create a distinctly named test crate and add only those generated tracks using Serato's supported UI.
3. Confirm three entries and their paths, then run Analyze Files. Verify no missing-file warning and load one tone into a deck with audio output under your control.
4. Record the observed app version, count, analysis/load outcome, and any errors. An M3U file by itself is not a native Serato crate.

Selected managed FLAC downloads now contain readable artist/title/version display tags, with incoming originals retained. This has engine/live-download evidence; Serato display compatibility still needs the native check.

## Physical USB and player gate

No external physical USB disk was connected during validation. A mounted app installer image is not a target USB. The engine does not format, write to, or create a native player database on storage.

Once the user supplies the exact mounted volume and player/controller model:

1. Run `usb-preflight` on that path with the actual collection byte requirement. Check that it is the intended mount and has sufficient capacity. The tool does not identify filesystem suitability or a player model.
2. Check the player's official supported filesystem/audio/export database formats before choosing native export settings. Preserve existing correctly prepared media; formatting is not a routine step.
3. Import/analyze the small test collection in rekordbox and export using rekordbox's native device workflow.
4. Read back the exported playlist/tracks in the app, eject normally, and load/play the test files on the target hardware.
5. Record per-stage outcomes. A folder capacity check, M3U/XML generation, or file copy cannot stand in for hardware playback evidence.

After the small gate passes, try a small owned-music collection before a large artist/set acquisition. Soulseek, complete discographies, automatic set recognition, musical categorization, and unattended app/USB automation are subsequent milestones in [the plan](../PLAN.md).
