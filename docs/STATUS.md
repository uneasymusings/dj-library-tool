# Implementation status

Updated 2026-10-03. Version 0.1.0a2. **Ready for supervised toolkit trials.** The complete product and unattended personal-library workflow remain unfinished.

## Implemented first milestone

- JSON CLI, 16 structured MCP tools, portable skill, and a separate project session generator for Codex/Claude Code. The skill ships in the wheel.
- Explicit workspace/source roots, authenticated localhost coordinator, SQLite/Alembic catalog, detached jobs, idempotency, generation fences, review revisions, and restart recovery.
- Local indexing, explicit collections, managed byte reuse, bounded publisher metadata inspection, selected public-source downloads.
- Full audio decoding/hashes, original acquisition provenance, chosen display tags on managed FLAC download copies, retained incoming originals.
- Hash-checked manifest/M3U/experimental rekordbox XML and read-only mount/capacity preflight.
- Runtime/lint/format/build CI for Linux/macOS/Windows with Python 3.12/3.13.

## Evidence and precise limits

| Area | Observed evidence | Remaining limit |
| --- | --- | --- |
| Local runtime | **115 tests passed** on macOS/Python 3.13, including a 200-item batch, review races, idempotency, partial retries, restart/fencing, auth, corruption, export readback, subprocess bounds/cancellation, CLI and MCP. | No multi-hour or 10,000-track performance claim. |
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
| USB | Read-only preflight tested for missing paths, folders, mount reporting, and capacity. No external physical disk was connected during the app check. | Target volume/player model required; no filesystem, export database, or hardware playback verification. |

The native rekordbox validation added only the three generated test tones through its own import UI. This is app import evidence, not an automatic native integration feature. Existing music was not batch imported or retagged. No device was formatted or written.

## Next acceptance gate

1. Follow [fresh session setup](AGENTS.md) and start with original demo audio or a small owned selection.
2. Complete the [Serato import and native XML checks](VALIDATION.md) in the installed app versions.
3. Connect the selected USB and provide player/controller models. Inspect storage, perform native rekordbox export, and verify on that hardware.
4. Exercise the user's chosen recordings; preserve provider failures and quality uncertainty.
5. Reconcile native tag changes before export; the current engine rejects changed catalog hashes rather than silently adopting them.

Do not call the milestone ready for unattended personal-library use until the dependent app/device gate passes.

## Still planned

Soulseek via slskd; provider discovery/ranking and quality policies; artist catalog enumeration; set tracklist reconciliation/acoustic recognition; missing/upgrade ledger; genre/BPM/key/energy analysis; native app reconciliation and automation; verified player-ready device export; standalone conversational CLI; early-web public home and optional local browser UX.

See [PLAN.md](../PLAN.md) for full scope and milestone acceptance criteria. No frontend has started.
