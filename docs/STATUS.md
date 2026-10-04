# Implementation status

Updated 2026-10-04. **0.1.0a3.dev0 is an unpublished local preview** for supervised trials. The public release remains 0.1.0a2; its wheel does not contain the target-delivery additions. The complete product and unattended personal-library workflow remain unfinished.

## Implemented first milestone

- JSON CLI, structured MCP tools, portable skill, and a separate project session generator for Codex/Claude Code. The skill ships in the wheel.
- Explicit workspace/source roots, authenticated localhost coordinator, SQLite/Alembic catalog, detached jobs, idempotency, generation fences, review revisions, and restart recovery.
- Local indexing, explicit collections, managed byte reuse, bounded publisher metadata inspection, selected public-source downloads.
- Full audio decoding/hashes, original acquisition provenance, chosen display tags on managed FLAC download copies, retained incoming originals.
- Hash-checked manifest/M3U/experimental rekordbox XML and read-only mount/capacity preflight.
- Runtime/lint/format/build CI for Linux/macOS/Windows with Python 3.12/3.13.

## Development: target-aware DJ delivery

- Persisted delivery requests freeze collection membership and selected recording identities; pilot/full phases separate small target trials from broad preparation.
- Durable preparation creates isolated, labeled app working copies and playlist/manifest artifacts. Preserve is the default; compatibility conversion is explicit. Catalog originals remain unchanged.
- Four sourced hardware profiles distinguish CDJ-2000NXS, CDJ-3000, XDJ-RX3 and OPUS-QUAD audio/library requirements. Unknown metadata and unknown targets cannot silently pass audio checks. Firmware and physical hardware remain unverified.
- Typed, revision-checked observations record native import, analysis, export, device-library inspection and playback as operator reports. They are not automatic native app integration.
- Passing rekordbox hardware-playback reports require the matching player profile, actual firmware version and successful storage recognition. CDJ-3000 firmware 3.30 is rejected, including `v3.30`/`V3.30` spellings.
- Read-only device verification checks volume identity and expected post-analysis audio hashes. Database filenames are existence markers only; their contents, membership and analysis are not parsed.
- Full preparation requires a completed matching pilot. Fresh `verify-device` evidence is required for the departure flag; an ordinary status query does not rehash audio.
- Analysis reconciliation is synchronous. Unchanged file hashes avoid redundant decoding; changed working copies require stream/audio checks. Observation requests have a 600-second client timeout, so large changed batches remain a practical limit rather than a background-job guarantee.

See [DJ delivery](DJ_DELIVERY.md) for both native workflows, commands, exact evidence boundaries and organization suggestions. **No new physical-player or complete native USB export validation is claimed by these additions.**

## Evidence and precise limits

Unless marked as preview/development evidence, the observations below describe the published 0.1.0a2 baseline.

| Area | Observed evidence | Remaining limit |
| --- | --- | --- |
| Local runtime | **Preview: 206 tests passed in 34.78 seconds** on macOS/Python 3.13.3 with FFmpeg; no skips. Published baseline: **115 tests passed** on macOS/Python 3.13, including a 200-item batch, review races, idempotency, partial retries, restart/fencing, auth, corruption, export readback, subprocess bounds/cancellation, CLI and MCP. | No multi-hour or 10,000-track performance claim. |
| Audio | Real WAV, FLAC, MP3, M4A, and AIFF decoding locally. Managed FLAC labels and original/final hashes checked. | Lossless output does not prove lossless source fidelity; no acoustic identity. |
| MCP | All 16 tools exercised through real MCP/ASGI/coordinator contracts; actual stdio subprocess reconnects and continues accepted work. | CI uses source fixtures, not live accounts. |
| Codex | Fresh **0.160.0** ephemeral sessions discovered the project skill, used MCP, completed two-tone collections/exports, and accurately reported `not_exported`; the latest trial used only the public GitHub-installed package outside the checkout. | Controlled end-to-end behavior trials; no broad model reliability claim. |
| Claude Code | Fresh **2.1.288** session completed a two-tone collection/export; second trial loaded `/dj-library`, checked items/reviews/collection, and correctly rejected an ordinary folder as USB evidence. | Project settings must remain enabled for skill discovery; disabling all setting sources suppressed it in the first trial. |
| Live sources | Bandcamp extractor fixture, SoundCloud publisher-declared CC0 track, and Blender's YouTube open movie each inspected, downloaded, decoded, tagged into managed FLAC, and cataloged successfully. | Three individual successes do not establish universal provider compatibility or DJ source quality. Removed YouTube fixture failed. |
| Dependencies | Locked source environment plus optional yt-dlp. FFmpeg/ffprobe and Node 22.15.0 present. Deno absent; Node fallback enabled. | Runtime availability does not guarantee a provider will accept extraction. |
| Packaging | [v0.1.0a2 GitHub release](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a2) published; anonymous artifact downloads/checksums and a fresh public-URL tool installation outside the checkout passed. Installed-wheel smoke check passed demo/migration/export, bundled skill setup, and actual stdio with all 16 tool schemas. | There is no PyPI release. |
| Cross-platform | The release commit passed runtime, lint, formatting, build, installed-wheel, and skill/checksum packaging on [all six OS/Python combinations](https://github.com/uneasymusings/dj-library-tool/actions/runs/37155504190). | FFmpeg cases skip when unavailable; native app evidence is macOS only. See the latest Actions run for matrix outcomes. |
| rekordbox | **7.2.8**: generated M3U8 imported three original WAV tones; native waveforms/key analysis appeared in Collection. | Native XML import, playlists with real music, USB export, and player playback pending. |
| Serato | **DJ Pro 3.1.5** launched and its Files/library interface was visible. | Import/analysis not verified: the computer-use service timed out, then screen capture failed. No direct crate/database write performed. |
| USB release baseline | Read-only preflight tested for missing paths, folders, mount reporting, and capacity. No external physical disk was connected during the original app check. | This historical preflight evidence does not validate a native device library or hardware playback. New development readback is described separately below. |
| Delivery preview | Preview profiles, real working-copy conversions, simulated evidence/readback, concurrent updates and transports passed within the 206-test suite. The installed wheel exposed 23 MCP tools and prepared three original tones. Machine evidence and operator observations have separate labels. | Native target trial remains a release gate. Hash presence and database markers do not establish playable native playlists. |

The native rekordbox validation added only the three generated test tones through its own import UI. This is app import evidence, not an automatic native integration feature. Existing music was not batch imported or retagged. No device was formatted or written.

## Next acceptance gate

1. Follow [fresh session setup](AGENTS.md) and start with original demo audio or a small owned selection.
2. Complete the [Serato import and native XML checks](VALIDATION.md) in the installed app versions.
3. Complete a [small target delivery](DJ_DELIVERY.md) using the actual app version, player/controller model and firmware, intended volume, native export/copy, device-library inspection, readback and physical playback. Record exact IDs/counts and retain any failed stages. Exercise rekordbox and Serato independently before claiming both workflows validated.
4. Exercise the user's chosen recordings; preserve provider failures and quality uncertainty.
5. Exercise native tag changes on isolated delivery working copies and confirm decoded-audio preservation and post-analysis hash readback. Changed original catalog hashes still require separate reconciliation.

Do not call the milestone ready for unattended personal-library use until the dependent app/device gate passes.

## Still planned

Soulseek via slskd; provider discovery/ranking and quality policies; artist catalog enumeration; set tracklist reconciliation/acoustic recognition; missing/upgrade ledger; genre/BPM/key/energy analysis; general native app reconciliation and automation; independently verified player-ready device export; standalone conversational CLI; early-web public home and optional local browser UX.

See [PLAN.md](../PLAN.md) for full scope and milestone acceptance criteria. No frontend has started.
