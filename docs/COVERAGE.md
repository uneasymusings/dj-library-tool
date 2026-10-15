# User workflow and public-release coverage

Updated 2026-10-05 UTC for public **a6**. The six-job release matrix, anonymous public-wheel installation and installed a3-to-a6 demo-copy compatibility check passed. Actual MCP checks exposed all 40 tool schemas and exercised three original tones, requests, organization and bundled skills. One earlier Windows PR discovery failure remains unexplained despite later passing matrices. a4/a5 tags remain immutable unpublished candidates; these release checks do not complete [PLAN.md](../PLAN.md). See [status](STATUS.md) and the audit below.

Version a6 retains the 40-tool catalog/delivery API and external Windows coordinator startup. It adds bounded startup readiness with distinct busy/failed/timeout recovery. The a4/a5 local, installed-wheel, upgrade and unsuccessful UI trials below remain historical only. No native automation, musical inference, historical identity repair or independently verified player-ready export is added.

New in 0.1.0a15: set identification (`djlib identify`, and `djlib set LINK` when a set's tracklist is missing or has IDs) listens to a YouTube or SoundCloud set and merges what it heard with published and given tracklists into one timeline with a status and evidence per track (listener comments only suggest candidates for IDs); `djlib set shazam` reads the user's Shazam history. The R01/R02 rows below note what that covers and what stays open.

New in 0.1.0a16: `djlib set` shows the whole set in set order with each track's time, confidence and whether the exact mix is owned; missing songs end in a shopping list of store searches (djlib never buys); a copied link or tracklist is read with `djlib set paste` (or `-` from a pipe); Shazam starts with the last 30 days when there are more than 100 songs; and the review page (`djlib ui`) opens on a Sets view. Matching also counts a song heard under other credits as owned under each tracklist's spelling, offers a file under a shorter or longer artist credit as “maybe yours” for the DJ to confirm, and folds “and”/“&” and a remixer's country tag, always for the exact mix. See R01, R02 and R12.

New in 0.1.0a17: `djlib set LINK` listens when the set's page names only the set itself (Boiler Room's one-line descriptions) or too few songs for the set's length, a tracklist written “Title - Artist” is read the way Shazam heard it, and a set download the site refuses, or that misses a piece, is tried once more. See R01 and R02.

## Requirement matrix

| Requirement | Public v0.1.0a2 baseline | a3 baseline / a6 / remaining work |
| --- | --- | --- |
| R01 — Accept set links | Bounded publisher metadata, description and chapter inspection for supported URLs. | `djlib set LINK` (since 0.1.0a11) takes a YouTube or SoundCloud set's tracklist from its description or chapters, listener comments as hints for IDs, and (since 0.1.0a15) MixesDB, then listens when the tracklist is missing or has IDs (since 0.1.0a17 also when it names too few songs for the set's length; a description line about the set itself, such as Boiler Room's “Artist – live from …”, is not a song). Since 0.1.0a15 each identified set keeps a persisted timeline (page, listening windows, given tracklists, merged tracklist) that later tracklists merge into without new listening. Since 0.1.0a16 a link or tracklist can be pasted (`djlib set paste`, `-`), and 1001Tracklists and Mixcloud links say what works instead. 1001Tracklists is never fetched: its tracklist is copied by hand or by an assistant in a browser. Mixcloud can't be listened to. Complete occurrence reconciliation (blends, repeats, edits) remains unfinished. |
| R02 — Identify and source every track in a set | Assistant search and selected individual recording URLs; no acoustic identification. | Unknown IDs and ambiguous versions must remain explicit. Since 0.1.0a15: song recognition (Shazam by default, unofficial; AudD or ACRCloud with the user's keys) over 12-second windows every 30 seconds (every minute with a timed tracklist), merged with published and given tracklists, with listener comments as candidates for IDs; each track is confirmed, likely, weak, conflict or unidentified, and an ID's likely song stays a candidate. Coverage is reported, not promised: songs the services don't know, short blends and heavy edits stay IDs, and a song heard in one window is never a track. Live end to end on 2026-10-13 from an installed wheel: a 62-minute YouTube set gave 13 of 13 rows and a 42-minute SoundCloud set 22 of 22 with its MixesDB tracklist (13 from audio alone); listening took 7 to 32 minutes depending on Shazam's rate limits. Since 0.1.0a16 the set screen shows every track with its time and confidence, owned or missing, with store searches for the missing ones. Since 0.1.0a17 a listed line that Shazam heard with artist and title swapped is that song, and a list with three or more such lines and none heard as written is read title first throughout. Live listens with 0.1.0a16 and 0.1.0a17 are in [status](STATUS.md). |
| R03 — Acquire large selections reliably | Durable selected-URL jobs, per-item outcomes, retries, pause/resume/cancel, byte dedupe and conflict reviews. | Adds fair item-boundary scheduling and responsive handoff work. Provider discovery/ranking, purchase flows and large-scale/live reliability benchmarks remain incomplete. |
| R04 — Collect an artist's work | Assistant-curated selections and explicit collections. | Adds a persisted exact-request ledger with explicit versions, owned matches, unresolved IDs and missing reports. It is bounded song-list reconciliation, not catalog enumeration, credit/alias expansion, complete-discography evidence or continuing sync. |
| R05 — Soulseek | Not implemented. | Still unresolved: no slskd search, peer browsing, transfer or recovery integration. |
| R06 — Organize for both DJ apps | Manifest/M3U8/experimental XML; basic rekordbox tone import/analysis evidence. | a3 adds app-first plans without a player model, byte-bound annotations, ordered collections and native XML comparison. a6 makes organization and native working-file checks durable jobs and adds explicit catalog reconciliation; old annotations/memberships do not silently move to changed bytes. Native actions remain operator-assisted; snapshot matches do not prove analysis/loading. No inferred BPM/key/genre/energy or native cue editing. |
| R07 — Prepare USB use | Read-only mount/capacity preflight; generic handoff artifacts. | a3 adds player-specific rekordbox and separate Serato-portable working-copy/evidence workflows. a6 permits unvalidated full local preparation without a physical pilot and adds bounded Windows partition metadata. Readiness still requires native export/copy, device checks and physical playback; pilot/full native cues are not reused automatically. |
| R08 — Conversational use through an existing host | JSON CLI, 16 MCP tools, complete skill and separate Codex/Claude sessions, with actual host trials. | a3 has 34 tools; a6 retains 40 plus external Windows pre-start and bounded startup recovery; 0.1.0a16 and 0.1.0a17 have 45, 22 by default. Actual 40-tool MCP/public-wheel and Windows lifecycle tests passed. Signed-in host trials remain historical; neither runner tests nor package installation establish general model reliability. |
| R09 — Standalone conversational CLI | Not implemented; requires an existing assistant host. | Still unresolved: no independent model adapter/chat loop. |
| R10 — Public, reusable GitHub toolkit | Installable wheel, source archive, standalone skill, checksums, MIT license, docs, CI and original-tone demo. | Public a3 passed artifact/install checks and six-job CI. a4/a5 remain unpublished after failed candidate checks. a6 is published; its six-job release matrix, anonymous artifact/installation audit and installed demo-copy compatibility checks passed. Historical audits do not complete native/hardware or broader goals. |
| R11 — Public project website | Repository, release page and documentation are public. | The planned dedicated early-web project home is not implemented. |
| R12 — Optional local browser operation | Not implemented. | A local review page has shipped since 0.1.0a7 (`djlib ui`): three static files served by the workspace's loopback service, with the bearer token kept in the URL fragment and session storage, a strict Content-Security-Policy and the same authenticated JSON routes as the CLI. Since 0.1.0a16 it opens on Sets (each set as a strip of owned, missing, other mix and IDs; per track the time, confidence, ownership and store searches), with Crates (and the commands that put a crate in rekordbox or on USB) and Library. Its only writes are picking a copy for a song and building a crate. It has no parity with the CLI (no tracklist input, rekordbox or USB steps), and no independent browser security review has been done. |

The request ledger preserves `satisfied`, `missing`, `ambiguous`, `unknown`, `unavailable` and `source_selected` outcomes. It matches normalized artist/title/version labels exactly and requires explicit selection when several byte revisions match. Create, refresh and resolution check files within bounded work (1 GiB/30 seconds and at most 20 candidates per item); incomplete checks stay visible. Use item-specific refreshes for larger requests. A read returns saved evidence with timestamps, not a new availability check. Selecting a source URL does not search or download it, and a local missing result does not mean no recording exists online. Its 22 focused tests passed before final transport integration.

Organization reads hash-matching embedded tags and stores explicit notes, categories, set roles, energy and BPM/key provenance against a recording's selected byte revision. Known tags start unverified; `native_tag` annotations must match freshly read tags. It does not extract analysis from native databases or delivery working copies. Delivery freezes explicit annotations and prepares working-copy tags/comments, retaining exact values/provenance in its manifest; native display and musical accuracy still need checking. Collections accept at most 1,000 explicit references, preserve overlap, support deterministic ordering and report exclusion reasons. Unknown filter evidence has explicit exclude/include/error behavior; unknown sort values remain last. Key filters match supplied labels; wheel labels sort numerically without harmonic translation. Thirteen focused organization tests passed, including real five-format metadata, concurrency and immutable-source checks. These additions cover deliberate local organization, not autonomous musical judgment or complete native integration. Final combined runtime/transport results belong in [STATUS.md](STATUS.md).

## Practical completion boundaries

| User request | Completion evidence needed |
| --- | --- |
| “Make sure these songs are downloaded.” | Enumerated requests, explicit versions, inspected accepted files, existing-catalog reuse, and a missing/ambiguous/unavailable report. A selected source or empty search result does not establish acquisition or unavailability everywhere. |
| “Organize by BPM/key/mood.” | Measured or explicitly supplied BPM/key evidence, native analysis review, and separate subjective tags. Suggested energy or role is not a measurement. |
| “Put this in rekordbox/Serato.” | Prepared files, native import/analysis observations covering recording IDs and playlist counts, then fresh working-file checks. A hardware model is unnecessary for this app-only scope. |
| “Make my USB ready.” | Correct destination workflow, volume identity, native export/copy, actual device-library inspection, byte readback and reported playback scope. Raw files and a database marker cannot establish this. |
| “Get everything from this artist/set.” | A declared catalog/timeline scope and explicit uncovered entries. Set identification reports how much of a set it named and leaves the rest as IDs; it cannot claim every track, and the toolkit cannot claim exhaustive discography enumeration. |

App-only readiness never sets USB departure readiness. Hardware uncertainty blocks dependent standalone export, not library import and analysis. See [DJ delivery](DJ_DELIVERY.md) for the separate workflows and the distinction between operator reports and machine checks.

## Independent public a6 distribution audit

The public wheel URL was installed with `[download]` into a new persistent uv tool environment outside the checkout. Downloads were anonymous; existing installations, configuration and music were preserved.

| Check | Observed result |
| --- | --- |
| Public release | [v0.1.0a6](https://github.com/uneasymusings/dj-library-tool/releases/tag/v0.1.0a6), published **2026-10-05 at 07:14:53 UTC**; prerelease, not draft. |
| Source and CI | Tag [`282f92f9d75b36e7813ad3a9d93d215efbe7eacd`](https://github.com/uneasymusings/dj-library-tool/commit/282f92f9d75b36e7813ad3a9d93d215efbe7eacd) passed the [six-job release matrix and artifact packaging](https://github.com/uneasymusings/dj-library-tool/actions/runs/37275004527). Both Windows jobs: 521 passed, two skips. |
| Artifact integrity | Exactly four assets: wheel, source archive, complete skill ZIP and SHA256SUMS. All three package hashes matched; wheel and ZIP skill resources matched byte for byte. |
| Installed provenance | Version `0.1.0a6`; module resolved in isolated tool-environment site-packages, with installed bytes matching the public wheel. No developer checkout or editable installation. |
| Actual MCP and workflows | The checksummed public source archive's smoke script passed three original tones, all 40 stdio MCP schemas, request/organization calls and bundled skill setup. |
| Persistent demo/session | A separate three-tone demo completed generic export and created an assistant session. Doctor found FFmpeg/ffprobe, yt-dlp and supported Node; the coordinator stopped with `url: null`. |
| Installed a3-to-a6 compatibility | A separate demo-library copy preserved three recordings/revisions/memberships, one collection, one nonempty annotation, all existing catalog tables and file locations, configuration/roots and media/export bytes. All 12 original files and seven copied immutable files were unchanged. Coordinators stopped; the original workspace remained untouched. Both versions used the same schema head, so this was binary compatibility, not a new migration. |
| Evidence boundary | No new signed-in AI-host, live acquisition, native app import/analysis, physical USB export or player trial occurred. A small demo-copy upgrade does not validate every real-library history. |

Verified public package SHA-256 values:

| Public artifact | SHA-256 |
| --- | --- |
| `dj_library_tool-0.1.0a6-py3-none-any.whl` | `767367d37491fef8eb77826c02ed42e8c71b1cd8d04f41a0b9c0331cca839f28` |
| `dj_library_tool-0.1.0a6.tar.gz` | `0ebe3f8b6b2601da06822b46c01123ee10e1d447625c4fb834b4b225043a25f7` |
| `dj-library-skill-0.1.0a6.zip` | `522771a7507f6fda0c655517e02bdfc05ac7cadaa8360a00838bff0873be9a5b` |

Later test-only diagnostics passed all six [push](https://github.com/uneasymusings/dj-library-tool/actions/runs/37275766324) and [PR](https://github.com/uneasymusings/dj-library-tool/actions/runs/37275770541) jobs, with production code and packaged skill resources identical to the release tag. An earlier Windows PR discovery failure remains unexplained; [validation](VALIDATION.md#a6-public-release-validation) records it without claiming a causal fix.

## Historical independent public a3 distribution audit

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
- The public a2 baseline has a quality workflow, without automated GitHub release creation. a3 adds a tag-triggered workflow that first runs the quality matrix, checks tag/package version agreement, checks the newly built wheel, and creates a **draft** prerelease with exactly the current four artifacts. Publishing the draft and verifying public URLs are separate steps; both completed for a3 and a6. Actual run results are recorded in [status](STATUS.md).
- Public wheel dependency ranges can resolve newer packages than `uv.lock`; normal installation is documented accordingly. Maintain both locked development tests and fresh public-wheel installation checks.
- The installed-wheel smoke checks tool names against a shared manifest, exercises request/annotation/organization tools and prepares an app-only rekordbox pilot through MCP. It uses original tones and synthetic app-version evidence only; it cannot establish native app import or analysis. It passed against the release builds and freshly published a3/a6 artifacts.
- The install guide, README and packaged skill target matching published a6 artifact URLs. Anonymous download/install verification passed. The preserved a4/a5 tags are unpublished; a3 URLs/checksums retain their independent audit evidence.

## Native app trial and remaining acceptance gate

Use a uniquely named small collection of original generated audio and isolated working copies. Import only that selection, inspect exact membership and paths, analyze only those tracks, and verify load/play behavior plus preservation after reopening the app. Keep existing collections and manually edited analysis intact. Short pure tones can test import, waveform generation and persistence; they cannot demonstrate musically correct BPM, key or beatgrids.

For rekordbox, use its documented M3U8 import and selected-track analysis. The Free plan includes EXPORT mode, so an unknown standalone player does not prevent app preparation. [rekordbox manual](https://cdn.rekordbox.com/files/20260807093645/rekordbox7.2.18_manual_EN.pdf), [plan FAQ](https://rekordbox.com/en/support/faq/plans/).

For Serato, create a uniquely named regular crate using the Files panel and keep the referenced working paths stable. Analyze the chosen crate, not the entire existing library. Serato 3.3.5 and earlier require disconnected DJ hardware for analysis. These native operations remain outside the engine. [Serato import](https://support.serato.com/hc/en-us/articles/223446528-Adding-files-to-the-Serato-DJ-Pro-Library), [Serato analysis](https://support.serato.com/hc/en-us/articles/14361068095759-Analyzing-Files).

Before publishing new compatibility claims, record the exact app versions, input formats, selected recording IDs, observed membership/analysis, preservation checks and trial scope. Repeat public-artifact installation for future releases. Keep failed or unavailable native/device checks visible in [STATUS.md](STATUS.md); neither the existence of GitHub assets nor the new local code completes all of PLAN.md.
