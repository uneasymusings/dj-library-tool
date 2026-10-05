# User workflow and public-release coverage

Updated 2026-10-04 for **v0.1.0a3**. This matrix compares a3 capabilities with the historical public a2 baseline and tracks the core requirements in [PLAN.md](../PLAN.md). It does not claim every milestone is complete. [Status](STATUS.md) records current publication, CI and native-trial evidence separately from implemented features.

## Requirement matrix

| Requirement | Public v0.1.0a2 baseline | v0.1.0a3 / remaining work |
| --- | --- | --- |
| R01 — Accept set links | Bounded publisher metadata, description and chapter inspection for supported URLs. | An assistant can interpret published tracklists. Persisted set timelines and complete occurrence reconciliation remain unfinished. |
| R02 — Identify and source every track in a set | Assistant search and selected individual recording URLs; no acoustic identification. | Unknown IDs and ambiguous versions must remain explicit. No automatic recognition of every track, blend or repeated occurrence. |
| R03 — Acquire large selections reliably | Durable selected-URL jobs, per-item outcomes, retries, pause/resume/cancel, byte dedupe and conflict reviews. | Adds fair item-boundary scheduling and responsive handoff work. Provider discovery/ranking, purchase flows and large-scale/live reliability benchmarks remain incomplete. |
| R04 — Collect an artist's work | Assistant-curated selections and explicit collections. | Adds a persisted exact-request ledger with explicit versions, owned matches, unresolved IDs and missing reports. It is bounded song-list reconciliation, not catalog enumeration, credit/alias expansion, complete-discography evidence or continuing sync. |
| R05 — Soulseek | Not implemented. | Still unresolved: no slskd search, peer browsing, transfer or recovery integration. |
| R06 — Organize for both DJ apps | Manifest/M3U8/experimental XML; basic rekordbox tone import/analysis evidence. | App-first `rekordbox_import` and `serato_import` prepare separate copies and track native import/analysis without requiring a player model. Adds byte-bound annotations, ordered collections and native rekordbox XML comparison of exact paths/membership/order. Native actions remain operator-assisted; snapshot matches do not prove analysis or loading. No automatic BPM/key/genre/energy inference or native cue editing. |
| R07 — Prepare USB use | Read-only mount/capacity preflight; generic handoff artifacts. | `rekordbox_usb` uses an exact player profile; `serato_portable` is a separate computer-library workflow. Working copies, operator evidence and read-only byte/volume checks are implemented. The native app owns export/copy; physical playback and preservation trials remain required. |
| R08 — Conversational use through an existing host | JSON CLI, 16 MCP tools, complete skill and separate Codex/Claude sessions, with actual host trials. | JSON CLI, 34 MCP tools, bundled workflow skill and separate sessions. Installed-package checks exercise request/organization/delivery transports. A successful smoke check is not broad model-reliability evidence; the historical host trials do not cover every new tool. |
| R09 — Standalone conversational CLI | Not implemented; requires an existing assistant host. | Still unresolved: no independent model adapter/chat loop. |
| R10 — Public, reusable GitHub toolkit | Installable wheel, source archive, standalone skill, checksums, MIT license, docs, CI and original-tone demo. | Builds the same release artifacts and adds validation-gated draft-release automation. Publishing and fresh public-artifact checks are separate release steps; see current [status](STATUS.md). The independent a2 installation audit is retained below. |
| R11 — Public project website | Repository, release page and documentation are public. | The planned dedicated early-web project home is not implemented. |
| R12 — Optional local browser operation | Not implemented. | Still subject to the product decision in PLAN.md; no local browser UI parity or browser security validation claim. |

The request ledger preserves `satisfied`, `missing`, `ambiguous`, `unknown`, `unavailable` and `source_selected` outcomes. It matches normalized artist/title/version labels exactly and requires explicit selection when several byte revisions match. Create, refresh and resolution check files within bounded work (1 GiB/30 seconds and at most 20 candidates per item); incomplete checks stay visible. Use item-specific refreshes for larger requests. A read returns saved evidence with timestamps, not a new availability check. Selecting a source URL does not search or download it, and a local missing result does not mean no recording exists online. Its 22 focused tests passed before final transport integration.

Organization reads hash-matching embedded tags and stores explicit notes, categories, set roles, energy and BPM/key provenance against a recording's selected byte revision. Known tags start unverified; `native_tag` annotations must match freshly read tags. It does not extract analysis from native databases or delivery working copies. Delivery freezes explicit annotations and prepares working-copy tags/comments, retaining exact values/provenance in its manifest; native display and musical accuracy still need checking. Collections accept at most 1,000 explicit references, preserve overlap, support deterministic ordering and report exclusion reasons. Unknown filter evidence has explicit exclude/include/error behavior; unknown sort values remain last. Key filters match supplied labels; wheel labels sort numerically without harmonic translation. Thirteen focused organization tests passed, including real five-format metadata, concurrency and immutable-source checks. These additions cover deliberate local organization, not autonomous musical judgment or complete native integration. Final combined runtime/transport results belong in [STATUS.md](STATUS.md).

## Practical completion boundaries

| User request | Completion evidence needed |
| --- | --- |
| “Make sure these songs are downloaded.” | Enumerated requests, explicit versions, inspected accepted files, existing-catalog reuse, and a missing/ambiguous/unavailable report. A selected source or empty search result does not establish acquisition or unavailability everywhere. |
| “Organize by BPM/key/mood.” | Measured or explicitly supplied BPM/key evidence, native analysis review, and separate subjective tags. Suggested energy or role is not a measurement. |
| “Put this in rekordbox/Serato.” | Prepared files, native import/analysis observations covering recording IDs and playlist counts, then fresh working-file checks. A hardware model is unnecessary for this app-only scope. |
| “Make my USB ready.” | Correct destination workflow, volume identity, native export/copy, actual device-library inspection, byte readback and reported playback scope. Raw files and a database marker cannot establish this. |
| “Get everything from this artist/set.” | A declared catalog/timeline scope and explicit uncovered entries. The current toolkit cannot claim exhaustive discography enumeration or acoustic set recognition. |

App-only readiness never sets USB departure readiness. Hardware uncertainty blocks dependent standalone export, not library import and analysis. See [DJ delivery](DJ_DELIVERY.md) for the separate workflows and the distinction between operator reports and machine checks.

## Independent public distribution audit

The audit used anonymous public artifact downloads and the published `uv tool install --python 3.13 'dj-library-tool[download] @ …v0.1.0a2…whl'` command in new, isolated tool/bin directories outside the checkout. It preserved existing installations, configurations and music. No original developer checkout was needed by the installed engine.

| Check | Observed result |
| --- | --- |
| Public release | [v0.1.0a2](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a2), published October 3, 2026; prerelease, not draft. |
| Source identity | Tag points to [`d9974321ba879a909e2308e9ce67902fd4daeab8`](https://github.com/uneasymusings/dj-library-tool/commit/d9974321ba879a909e2308e9ce67902fd4daeab8); its [tag CI run passed](https://github.com/uneasymusings/dj-library-tool/actions/runs/37155724379). |
| Artifact inventory | Wheel, source archive, standalone skill ZIP and SHA256SUMS. All three downloadable packages matched the published checksum file. |
| Wheel identity | 53,813 bytes; SHA-256 `1dea7437a8b7a90a8fba3872e830f101c0a2cd8122c383b911a43f09a8ecc3a2`. |
| Ordinary installation | Public URL installed successfully with Python 3.13.3; 54 dependency packages resolved. `djlib version` returned `0.1.0a2`. |
| Included resources | CLI entry point, engine, original catalog migration, MCP server, SKILL.md and both CLI/install references. Source archive also includes uv.lock and build scripts. |
| Original-tone trial | Three generated tones ingested; generic export completed. No personal audio or native app database was used. |
| Session generation | Both project skills, launch.py, MCP configuration and session metadata generated in a new directory; personal configuration unchanged. |
| Actual MCP call | Installed stdio server exposed 16 tools; `djlib_capabilities` succeeded and reported a2. The separate coordinator was stopped and its lock release checked. |
| Version boundary | a3 delivery, app-target profiles, organization, exact-request tracking and native XML inspection are absent from the a2 wheel. |

This proves a public macOS/Python 3.13 installation and local core/MCP smoke path on the audit date. It does not retest every live music provider, Windows/Linux native apps, or physical USB playback. Runtime dependencies are ranges rather than the contributor lockfile: this audit resolved, among others, MCP 2.3.0, SQLAlchemy 2.1.3 and yt-dlp 2026.8.19.

## Release-process findings

- CI pins the checkout/setup-uv actions and uv version, installs locked dependencies, runs tests/lint/formatting, builds packages, checks an installed wheel, and builds the standalone skill/checksums across Python 3.12/3.13 on Linux/macOS/Windows. That matrix does not itself exercise native DJ software or hardware.
- The skill ZIP uses fixed timestamps and an explicit file allowlist; its reproducibility is tested. The checksum builder selects the current version's wheel/source/skill rather than every old file in `dist/`. Whole-wheel/source bit-for-bit reproducibility is not established by that skill test.
- The public a2 baseline has a quality workflow, without automated GitHub release creation. a3 adds a tag-triggered workflow that first runs the quality matrix, checks tag/package version agreement, and creates a **draft** prerelease with built artifacts. Publishing the draft and verifying public URLs remain separate steps; actual run results are recorded in [status](STATUS.md).
- Public wheel dependency ranges can resolve newer packages than `uv.lock`; normal installation is documented accordingly. Maintain both locked development tests and fresh public-wheel installation checks.
- The installed-wheel smoke checks tool names against a shared manifest, exercises request/annotation/organization tools and prepares an app-only rekordbox pilot through MCP. It uses original tones and synthetic app-version evidence only; it cannot establish native app import or analysis. Run it against the final built wheel and freshly published artifacts.
- The install guide, README and packaged skill pin matching a3 artifact URLs. Verify those exact public assets after publication; preparing release documentation does not establish that an upload has completed.

## Native app trial and remaining release gate

Use a uniquely named small collection of original generated audio and isolated working copies. Import only that selection, inspect exact membership and paths, analyze only those tracks, and verify load/play behavior plus preservation after reopening the app. Keep existing collections and manually edited analysis intact. Short pure tones can test import, waveform generation and persistence; they cannot demonstrate musically correct BPM, key or beatgrids.

For rekordbox, use its documented M3U8 import and selected-track analysis. The Free plan includes EXPORT mode, so an unknown standalone player does not prevent app preparation. [rekordbox manual](https://cdn.rekordbox.com/files/20260807093645/rekordbox7.2.18_manual_EN.pdf), [plan FAQ](https://rekordbox.com/en/support/faq/plans/).

For Serato, create a uniquely named regular crate using the Files panel and keep the referenced working paths stable. Analyze the chosen crate, not the entire existing library. Serato 3.3.5 and earlier require disconnected DJ hardware for analysis. These native operations remain outside the engine. [Serato import](https://support.serato.com/hc/en-us/articles/223446528-Adding-files-to-the-Serato-DJ-Pro-Library), [Serato analysis](https://support.serato.com/hc/en-us/articles/14361068095759-Analyzing-Files).

Before publishing new compatibility claims, record the exact app versions, input formats, selected recording IDs, observed membership/analysis, preservation checks and trial scope. Run public-artifact installation after publishing. Keep failed or unavailable native/device checks visible in [STATUS.md](STATUS.md); neither the existence of GitHub assets nor the new local code completes all of PLAN.md.
