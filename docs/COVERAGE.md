# User workflow and public-release coverage

Updated 2026-10-05 UTC for **a4**. The matrix retains a2/a3 coverage and labels a4 additions; the public-audit sections below remain historical evidence. Local and installed-wheel checks passed, while a4 release CI and public-artifact verification remain pending. It does not claim publication or completion of every milestone in [PLAN.md](../PLAN.md). [Status](STATUS.md) separates implementation from actual validation.

Version a4 adds paginated saved-work discovery, explicit roots, safer new recording identity, changed-file reconciliation, durable organization/native checks and full local preparation without a hardware pilot. It does not add native app automation, musical inference, automatic repair of historical identity merges or independently verified player-ready export. The installed-wheel smoke check exercised 40 MCP tools and three original tones. Local validation passed 498 tests with four Windows-only skips; Ruff passed 95 files. These checks are separate from pending release CI/public verification and the public a3 34-tool audit.

## Requirement matrix

| Requirement | Public v0.1.0a2 baseline | a3 baseline / a4 / remaining work |
| --- | --- | --- |
| R01 — Accept set links | Bounded publisher metadata, description and chapter inspection for supported URLs. | An assistant can interpret published tracklists. Persisted set timelines and complete occurrence reconciliation remain unfinished. |
| R02 — Identify and source every track in a set | Assistant search and selected individual recording URLs; no acoustic identification. | Unknown IDs and ambiguous versions must remain explicit. No automatic recognition of every track, blend or repeated occurrence. |
| R03 — Acquire large selections reliably | Durable selected-URL jobs, per-item outcomes, retries, pause/resume/cancel, byte dedupe and conflict reviews. | Adds fair item-boundary scheduling and responsive handoff work. Provider discovery/ranking, purchase flows and large-scale/live reliability benchmarks remain incomplete. |
| R04 — Collect an artist's work | Assistant-curated selections and explicit collections. | Adds a persisted exact-request ledger with explicit versions, owned matches, unresolved IDs and missing reports. It is bounded song-list reconciliation, not catalog enumeration, credit/alias expansion, complete-discography evidence or continuing sync. |
| R05 — Soulseek | Not implemented. | Still unresolved: no slskd search, peer browsing, transfer or recovery integration. |
| R06 — Organize for both DJ apps | Manifest/M3U8/experimental XML; basic rekordbox tone import/analysis evidence. | a3 adds app-first plans without a player model, byte-bound annotations, ordered collections and native XML comparison. a4 makes organization and native working-file checks durable jobs and adds explicit catalog reconciliation; old annotations/memberships do not silently move to changed bytes. Native actions remain operator-assisted; snapshot matches do not prove analysis/loading. No inferred BPM/key/genre/energy or native cue editing. |
| R07 — Prepare USB use | Read-only mount/capacity preflight; generic handoff artifacts. | a3 adds player-specific rekordbox and separate Serato-portable working-copy/evidence workflows. a4 permits unvalidated full local preparation without a physical pilot and adds bounded Windows partition metadata. Readiness still requires native export/copy, device checks and physical playback; pilot/full native cues are not reused automatically. |
| R08 — Conversational use through an existing host | JSON CLI, 16 MCP tools, complete skill and separate Codex/Claude sessions, with actual host trials. | a3 has 34 tools. a4 has 40 with an actual installed-wheel MCP smoke check, plus discovery/roots/reconciliation and queued progress. Release CI/public checks remain pending. Historical host trials do not establish new-tool or general model reliability. |
| R09 — Standalone conversational CLI | Not implemented; requires an existing assistant host. | Still unresolved: no independent model adapter/chat loop. |
| R10 — Public, reusable GitHub toolkit | Installable wheel, source archive, standalone skill, checksums, MIT license, docs, CI and original-tone demo. | Public a3 passed anonymous artifact/install checks and six-job CI. a4 passed local tests and an installed-wheel 40-tool smoke check; its release matrix and public-artifact audit remain pending. Historical audits below do not complete native/hardware or broader roadmap requirements. |
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

## Independent public a3 distribution audit

The ordinary published wheel URL was installed with its `[download]` extra into a new persistent uv tool environment outside the checkout. Downloads were anonymous, and existing installations, configuration and music were preserved.

| Check | Observed result |
| --- | --- |
| Public release | [v0.1.0a3](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a3), published **2026-10-05 at 04:15:02 UTC**; prerelease, not draft. |
| Source and CI | Tag [`05a8fff512f8b828897259b66614e0f42967c928`](https://github.com/uneasymusings/dj-library-tool/commit/05a8fff512f8b828897259b66614e0f42967c928) passed the [six-job release matrix](https://github.com/uneasymusings/dj-library-tool/actions/runs/37261484677). Windows: 404 passed, two POSIX-only skips. |
| Artifact integrity | Exactly four assets: wheel, source archive, skill ZIP and SHA256SUMS. All three package hashes matched; complete wheel/ZIP skill resources and license matched byte for byte. |
| Installed provenance | Version `0.1.0a3`; module resolved in isolated tool-environment site-packages, with installed package bytes equal to the public wheel. No developer checkout or editable installation. |
| Actual MCP and workflows | The checksummed public source archive's smoke script passed three original tones, all 34 stdio MCP tool schemas, request/annotation/organization calls and packaged skill setup. |
| Persistent demo/session | A separate three-tone demo completed generic export and generated both complete project skills in a separate assistant session. Doctor found FFmpeg/ffprobe, yt-dlp and supported Node; the coordinator stopped and status returned `url: null`. |
| Evidence boundary | No new signed-in AI-host, live acquisition, native app import/analysis, physical USB export or player trial occurred in this installation audit. |

Verified package SHA-256 values:

| Public artifact | SHA-256 |
| --- | --- |
| `dj_library_tool-0.1.0a3-py3-none-any.whl` | `2935d4e5f2c5b1b362d3193e441844eee0a71a9d96d613c30f8fb7ac1be5d0d3` |
| `dj_library_tool-0.1.0a3.tar.gz` | `06ec2e9ba99bd7cd11e941ee7d007fe2a8bf59d67fe0f2188d4b1190ed14ee44` |
| `dj-library-skill-0.1.0a3.zip` | `911ad0f715a426e6889fc8074b7cecaa85a6beed47cf5ea45cee3da643d10dda` |

This is a public macOS/Python 3.13 installation check with actual engine/MCP work. Separate [validation evidence](VALIDATION.md) records the tag matrix, later test-only diagnostic checks, and native-trial limits; passing installation does not establish the full personal-library workflow.

## Historical independent public a2 distribution audit

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
- The public a2 baseline has a quality workflow, without automated GitHub release creation. a3 adds a tag-triggered workflow that first runs the quality matrix, checks tag/package version agreement, checks the newly built wheel, and creates a **draft** prerelease with exactly the current four artifacts. Publishing the draft and verifying public URLs are separate steps; both completed for a3. Actual run results are recorded in [status](STATUS.md).
- Public wheel dependency ranges can resolve newer packages than `uv.lock`; normal installation is documented accordingly. Maintain both locked development tests and fresh public-wheel installation checks.
- The installed-wheel smoke checks tool names against a shared manifest, exercises request/annotation/organization tools and prepares an app-only rekordbox pilot through MCP. It uses original tones and synthetic app-version evidence only; it cannot establish native app import or analysis. It passed against the release build and freshly published a3 artifacts.
- The install guide, README and packaged skill target matching a4 artifact URLs. Publication and anonymous download/install verification for those URLs remain pending. The historical a3 URLs and checksums below retain their independent audit evidence.

## Native app trial and remaining acceptance gate

Use a uniquely named small collection of original generated audio and isolated working copies. Import only that selection, inspect exact membership and paths, analyze only those tracks, and verify load/play behavior plus preservation after reopening the app. Keep existing collections and manually edited analysis intact. Short pure tones can test import, waveform generation and persistence; they cannot demonstrate musically correct BPM, key or beatgrids.

For rekordbox, use its documented M3U8 import and selected-track analysis. The Free plan includes EXPORT mode, so an unknown standalone player does not prevent app preparation. [rekordbox manual](https://cdn.rekordbox.com/files/20260807093645/rekordbox7.2.18_manual_EN.pdf), [plan FAQ](https://rekordbox.com/en/support/faq/plans/).

For Serato, create a uniquely named regular crate using the Files panel and keep the referenced working paths stable. Analyze the chosen crate, not the entire existing library. Serato 3.3.5 and earlier require disconnected DJ hardware for analysis. These native operations remain outside the engine. [Serato import](https://support.serato.com/hc/en-us/articles/223446528-Adding-files-to-the-Serato-DJ-Pro-Library), [Serato analysis](https://support.serato.com/hc/en-us/articles/14361068095759-Analyzing-Files).

Before publishing new compatibility claims, record the exact app versions, input formats, selected recording IDs, observed membership/analysis, preservation checks and trial scope. Repeat public-artifact installation for future releases. Keep failed or unavailable native/device checks visible in [STATUS.md](STATUS.md); neither the existence of GitHub assets nor the new local code completes all of PLAN.md.
