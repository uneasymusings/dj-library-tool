# DJ Library Tool — Product and Implementation Plan

Version: 0.2 • Prepared: 2026-10-02 • Updated: 2026-10-05 • Status: full-scope blueprint with an experimental local toolkit; broader milestones and unresolved product preferences remain open.

Repository: https://github.com/uneasymusings/dj-library-tool. Command: **djlib**. Public GitHub distribution is implemented: a6 is published with a checksummed wheel, source archive and complete skill ZIP. Its six-job release matrix and independent anonymous public installation passed. a4/a5 remain immutable unpublished candidates after failed release checks; the a3 audit remains a separate historical baseline. See [implementation status](docs/STATUS.md) for exact code, publication and validation evidence.

### First usable milestone — existing LLM host toolkit

The user's 2026-10-03 direction prioritizes a portable skill plus tools for Codex and Claude Code, before a standalone chat interface or frontend. Deliver a local workflow for owned music, selected recording URLs, named collections, resumable jobs, metadata reviews, app handoff artifacts, and read-only USB preflight. The assistant uses its own discovery/search capabilities; the engine exposes strict commands and retains durable side effects.

This checkpoint draws the reusable foundation from M0/M1, selected acquisition and handoff capabilities from M2/M3/M7/M8, and skill/MCP packaging from M6. It does not mark those complete milestones as achieved. Soulseek, acoustic set recognition, automatic artist catalogs, native DJ integration, and verified USB export retain their full scope and acceptance gates below.

The implementation now includes a local coordinator/catalog, durable acquisitions and checks, exact-request ledgers, conservative recording/revision identity, explicit annotations and ordered collections, changed-file reconciliation, paginated saved work, app-first preparation and separate device workflows. The CLI/MCP and packaged skill/session generator share this engine. Native import, analysis, cues and export remain app-owned; the engine prepares isolated working copies and records attributed evidence. A player model is unnecessary for Serato/rekordbox app preparation. Full local preparation can proceed without a physical pilot while native/device readiness remains gated.

Current local source checks passed 518 tests with five Windows-only skips. The a6 release passed all six OS/Python jobs; anonymous public-wheel validation exercised 40 MCP tools, three original tones, request tracking, organization and complete skill setup outside the checkout. An installed a3-to-a6 check on a separate demo-library copy preserved identities, memberships, annotations, files and configuration; no schema migration was needed. One earlier Windows PR discovery check failed for an unproven reason; later diagnostic-only and release matrices passed without establishing a causal fix. These results do not prove native app/device readiness or every broader milestone. Existing Codex/Claude and live-provider trials retain their original versions/dates in [validation](docs/VALIDATION.md). Sections below remain the full target design, including interfaces and integrations not yet implemented; [CONTRACT](docs/CONTRACT.md) documents the actual current API.

Native evidence is limited and historical. The a3-era rekordbox trial imported three generated tones; its later native XML declared 7.2.19 and confirmed exact working paths/membership, differing from the trial's older declared 7.2.8 bundle metadata. That version mismatch prevented passing the trial's version check. Loading and musical BPM/key/grid accuracy remained unverified; this is not current-release native validation. The a4-candidate trial prepared three tones for each app but did not complete import because of disabled controls/computer-use errors; its rekordbox bundle build 7.2.19.0342 was not confirmed as the runtime version. Serato DJ Pro 3.1.5 import/analysis, real-music app preservation and physical USB/player delivery remain unverified. No physical USB was present in those release trials. Read About or native XML before declaring an app version; bundle build metadata is insufficient. These tests neither batch-organized the user's whole library nor established player readiness.

### Navigation

- Product: [scope and decisions](#1-purpose-and-how-to-use-this-plan), [success criteria](#2-product-model-and-success-criteria), [user journeys](#3-end-to-end-user-journeys), [automation and review](#4-preferences-review-and-automation).
- Engineering: [music data model](#5-music-identity-metadata-and-organization), [architecture](#6-architecture-and-local-execution), [durable jobs](#7-durable-jobs-idempotency-and-recovery), [CLI/MCP/skills](#8-exemplary-cli-and-agent-interfaces).
- Integrations: [providers and discovery](#9-provider-contracts-and-source-integrations), [DJ apps and USB](#10-serato-rekordbox-and-usb-integration), [later web experience](#11-later-web-experience-and-public-home).
- Delivery: [security](#12-security-privacy-and-operational-boundaries), [tests and evaluations](#13-validation-and-evaluation-strategy), [GitHub and packaging](#14-packaging-documentation-and-github-quality), [implementation work packages](#15-implementation-roadmap-and-work-packages), [risks](#16-risk-register-and-decision-checkpoints), [completion](#17-definition-of-complete-and-first-implementation-handoff).

Recommended baseline: local Python engine; one persistent coordinator; SQLite catalog; CLI and MCP from the foundation; slskd for Soulseek; replaceable extraction/catalog/recognition adapters; explicit music-version identity; staged reversible file changes; verified app/device adapters; later simple web surfaces. Decisions about user preferences remain provisional below.

## 1. Purpose and how to use this plan

Build an open-source local utility that lets a DJ describe the music they want, acquire available recordings in bulk, maintain a trustworthy collection, organize it musically, and use it in Serato, rekordbox, and compatible USB workflows. A DJ can operate it directly in a terminal, through an LLM CLI using MCP or a skill, through its own conversational command, and eventually through a deliberately simple local web interface.

The public GitHub project should demonstrate sound product judgment, useful agent interfaces, reliable background execution, music-specific matching, measured quality, and careful integration with existing software. The public website provides the early-web aesthetic requested by the user and a practical home for installation, documentation, and demonstrations.

This plan includes the complete requested product. Milestones sequence implementation; an early acquisition demo or XML export does not complete the full scope. The difficult integration work remains explicitly scheduled and has evidence-based release gates.

Implementation rules:

1. Resolve the decisions required by the current milestone; other open decisions do not stop independent work.
2. Implement complete vertical workflows with acceptance evidence. Do not claim an integration from an exporter that has never been opened in its target app.
3. Keep provider behavior, schemas, capabilities, and compatibility records versioned.
4. Amend this plan and the decision log when research changes a dependency or product promise.
5. Ship only capabilities supported by evidence. Keep experimental and assisted workflows visible without presenting them as unattended automation.
6. Do not start frontend implementation during the initial headless milestones.

### 1.1 Scope and requirement status

R01–R11 derive from the user's request. R12 is a proposed interpretation of the desired online UX and awaits D09; its detailed design is included so either choice can be implemented without reworking the engine.

| ID | User requirement | Completion evidence |
| --- | --- | --- |
| R01 | Accept DJ set links from YouTube, SoundCloud, and extensible additional sources | A supported URL produces a persisted set, timestamped identification results, uncertainty, and a source report. |
| R02 | Find track IDs and download sources throughout a set | Text and audio evidence are reconciled; known and unknown sections remain inspectable; viable recordings are sourced. |
| R03 | Acquire heaps of music reliably | Large persistent jobs support queues, retries, rate limits, pause, cancel, resume, dedupe, and useful partial outcomes. |
| R04 | Collect an artist's work through natural language | Catalog scope, alias/credit handling, acquisition, organization, and a persistent missing-items ledger work together. |
| R05 | Include Soulseek | A real provider handles search, optional peer browsing, candidate selection, transfers, queues, and recovery. |
| R06 | Organize for Serato and rekordbox | Both have documented, exercised import/export paths, compatibility records, and preservation checks. |
| R07 | Prepare music for USB use | Target-specific preflight and supported export produce a manifest and truthful device readiness evidence. |
| R08 | Work exceptionally well from LLM CLIs | Stable CLI JSON, MCP tools, a portable skill, setup guides, bounded responses, and conversational acceptance scenarios. |
| R09 | Offer direct conversational CLI use | A configurable model adapter translates natural language into the same typed application commands. |
| R10 | Be public and reusable on GitHub | Installable releases, license/attribution, documentation, CI, fixtures, evaluations, contributor instructions, and a useful credential-free demo. |
| R11 | Have a simple early-web online home | Accessible static site with install instructions, examples, docs, compatibility, and an honest demo. |
| R12 — proposed | Optional rudimentary local browser operation, subject to D09 | A local interface uses the same engine; its breadth follows the user's selected website role. |

“Every ID” and “all work” describe user intent. Report the catalog queried, coverage observed, and unresolved results; exhaustive identification and universal availability cannot be promised. An unrecognized segment must never acquire an invented artist/title.

### 1.2 Product decisions requested from the user

No response or preselected option counts as agreement. These defaults make the plan concrete and remain editable.

| Decision | Proposed baseline while unanswered | Required before |
| --- | --- | --- |
| D01 — Automation | Execute confident actions within saved preferences; collect exceptions without interrupting independent work. Offer whole-batch review mode. | First real acquisition UX. |
| D02 — Sources | Check owned media first. Rank exact eligible matches by quality across direct sources and Soulseek. Use configured web extraction as a fallback. | First real provider execution. |
| D03 — Lower-quality fallback | Keep it in a separate review collection and retain an upgrade target. Do not silently publish it into a performance crate. | Quality-policy implementation. |
| D04 — Native app and USB depth | Full target is minimal-intervention app import and compatible USB preparation; assisted paths are intermediate capabilities. | Native integration release claims. |
| D05 — Actual equipment | macOS first for development, Windows first-class for distribution; retain both DJ apps. Exact app versions and players are unknown. | Real app/device compatibility validation. |
| D06 — Project name | Published as DJ Library Tool / djlib; any future rebrand is a separate choice. | A deliberate branding change, not existing public distribution. |
| D07 — Existing organization | Index existing collections without moving them; use a separate managed root for new acquisitions. Preserve personal crates and tags. | Existing-library writes. |
| D08 — Recognition and model costs | Optional bring-your-own credentials; explicit per-job budgets; all local-library workflows and demo work without paid services. | Enabling billable integrations. |
| D09 — Website role | Public install/docs/demo home plus a small local control page; a broad browser interface is optional. | M9 scope and implementation. |

Follow-up discovery after the initial answers: existing library size and locations; preferred genres and file formats; willingness to run an optional Soulseek service; expected role of manual audition; exact target devices; preferred public license. These are configuration and release decisions, not reasons to delay the core design.

## 2. Product model and success criteria

### 2.1 The main promise

**A request becomes a traceable collection of the correct recordings, with useful organization and an honest report of what is ready to play.**

Design around four repeating user intentions:

- Discover: identify a set, inspect a catalog, or find a recording.
- Collect: fill gaps from configured sources and improve inferior copies.
- Organize: correct metadata and build useful crates without losing personal work.
- Prepare: hand collections to DJ software and the selected playback device.

Natural-language interaction is an interface to these operations. The collection engine also works through explicit commands without a model.

### 2.2 Completion is multidimensional

Expose these independently:

| Dimension | Example states |
| --- | --- |
| Identity | unknown, candidate, needs_review, accepted, rejected |
| Acquisition | owned, searchable, queued_at_source, downloading, acquired, purchase_required, unavailable |
| Validation | pending, passed, failed, warning, review_required |
| Library | staged, managed, indexed_external, audition_pending, ready |
| DJ app | prepared_for_import, import_requested, imported_verified, analysis_pending, analyzed_verified |
| Device | target_not_selected, preflight_passed, export_requested, exported_verified, playback_verified |

Do not collapse these into one long track-status enum. A file may be owned but the identity uncertain, or correctly imported but awaiting analysis. A job can finish with gaps while individual catalog items remain retryable.

Show “Unknown duration” or “unresolved sections,” not a fabricated count of unidentified songs. Set timeline coverage and recording identification precision are distinct metrics.

Every app/analysis/device observation also records verification_method, actor/adapter, timestamp, target version, and evidence reference. Distinguish adapter_readback, user_attested, and hardware_observed. A user-confirmed assisted import can be recorded as complete with that evidence method; it must not be presented as automated readback.

### 2.3 Defaults that reduce friction

- Remember profiles and the last relevant collection. Follow-up requests may reference stable job/collection IDs resolved from conversation context.
- Ask only when a decision materially changes identity, cost, existing user data, source access, or the target device.
- Confident tracks continue while exceptions enter a grouped review queue.
- A source failure should not disable unrelated sources or local functionality.
- Preserve explicit user edits above future automatic suggestions.
- Keep original audio and source provenance; make compatibility derivatives traceable.
- One recording can belong to many crates without copying the master file.
- Do not substitute a set excerpt, radio edit, remix, live version, or different master silently.
- Opening an app, changing a crate, and ejecting a device are observable operations with explicit results.

### 2.4 Product measures

Track results for measured datasets, with sample sizes and limitations:

- Time and user interventions from request to usable collection.
- Precision of auto-accepted recording/version matches.
- Percentage of independently annotated set occurrences correctly identified; timeline coverage reported separately.
- Wrong-version substitutions and undesired duplicates.
- Eligible-file acquisition success, source-specific failures, and time in peer queues.
- Restart/resume correctness and additional work avoided through caching.
- Preservation of original files, cue points, grids, ratings, and personal tags.
- Successful target-app imports and supported-player exports.
- Agent tool calls and response size needed to complete representative requests.

Targets later in this plan are proposed acceptance gates, not measured claims.

## 3. End-to-end user journeys

### 3.1 First use

1. Install the core package and run doctor. It reports capabilities, optional dependencies, available providers, storage access, and supported DJ integrations.
2. Run a credential-free demo in an isolated directory. Show a completed collection, one ambiguous version, and a recovered job.
3. Select a managed music directory and optional existing-library roots. Present the physical storage policy once.
4. Index existing media to avoid buying/downloading files already owned. Support cancellation and incremental continuation.
5. Connect sources individually. For Soulseek, connect an existing supported service or guide an explicit optional installation. For recognition/model APIs, explain budget settings at connection time.
6. Save a preference profile: sources, versions, quality, organization, limits, and target app/device.
7. Run the first real request. Reuse setup in CLI, MCP, conversational CLI, and future local web.

Secrets are entered through local setup or secret-management mechanisms, not pasted into an LLM conversation. Missing optional accounts produce capability limitations and repair instructions.

### 3.2 Set URL to crate

1. Normalize the URL; store its provider identity and source title. Reuse a prior source snapshot where appropriate.
2. Gather accessible descriptions, chapters, uploader tracklists, user-supplied lists, and permitted third-party references.
3. Parse candidate entries into structured evidence, including original text and source timestamps. Comments are weak evidence until corroborated.
4. Obtain suitable analysis audio through a supported adapter, or accept a local set file.
5. Run configured recognition and reconcile its results with textual evidence.
6. Preserve multiple simultaneous tracks during blends and repeated occurrences later in the set.
7. Resolve each candidate to a recording/version and release evidence. Separate recognition confidence from exact-version confidence.
8. Find matching owned files, then search sources for eligible missing recordings.
9. Acquire, validate, and tag accepted files. Keep wrong, incomplete, or uncertain results out of ready collections.
10. Create a source collection. Default crate membership deduplicates the same accepted recording while retaining first-play order; the timeline retains every occurrence. An occurrence playlist is an optional view.
11. Import/export using the chosen app profile. Return ready counts, app/device state, purchase links, and timestamped unresolved sections.
12. A later request can correct an ID, retry missing recordings, rescan only gaps, or change source/quality policy.

Acceptance example: a mixed set with repeated songs, an overlap, a deliberately wrong comment ID, and an unknown section produces a useful partial crate, retains the unknown timestamp, and does not promote the wrong comment into a confirmed ID.

### 3.3 Artist catalog to collection

1. Resolve artist identity using authoritative IDs where available. Ask about genuinely ambiguous names before acquisition.
2. Apply an explicit scope. Proposed default: primary-artist releases, tracks remixed by the artist, and credited collaborations/features. Aliases, production-only credits, remixes by other artists, DJ mixes, and unreleased material are separate switches.
3. Gather paginated catalog evidence and URL relationships from supported sources. Record source coverage and fetch dates.
4. Collapse repeated release appearances without erasing original/remix/extended/edit distinctions.
5. Compare the intended catalog with owned recordings and quality preferences.
6. Acquire missing files or stage eligible upgrades. Purchases remain linked review items until the user completes a supported purchase/import path.
7. Organize artist, originals, remixes, collaborations, release, and discovery-date collections.
8. Keep a persistent ledger of missing, unavailable, excluded, and unresolved items.
9. Support explicit sync to detect changed metadata, new releases, and newly available files. Recurring sync is opt-in with frequency, budgets, and per-source limits.

Acceptance example: “collect Joy Orbison’s work, including remixes, skip anything I own” resolves an artist, reports scope, reuses existing files, preserves distinct versions, and produces a coverage report. No claim of a complete lifetime discography is made from one catalog search.

### 3.4 Downloads folder to organized library

1. Scan supported audio and supported archives; ignore unrelated executable content.
2. Read tags, probe media, hash exact bytes, and compute optional fingerprints.
3. Resolve probable recording identities and duplicate relationships.
4. Generate a change plan: metadata corrections, managed copies, crate additions, suspected duplicates, and proposed categories.
5. Commit permitted additions; require scoped decisions for conflicting metadata, removal, replacement, or movement of external originals.
6. Maintain an Unidentified collection for valid files without reliable identity.
7. Keep a change journal and provide guarded undo for changes still owned by the tool.

Existing files are indexed in place by default. Copy-on-import creates a managed copy only when requested; adopting/moving an existing collection is a separate operation with a preflight and manifest.

### 3.5 Conversational correction and curation

Examples the finished tool must support:

- “That ID at 42 minutes is the dub, not the vocal version.”
- “Put the darker tracks from this batch into my warm-up crate.”
- “Only retry Soulseek results that meet my club profile.”
- “Show me tracks I downloaded this week that I haven’t auditioned.”
- “Keep the original files; make compatible copies for this player.”
- “Undo the crate changes from the previous job.”

A correction targets a stable entity and revision. Re-resolve dependent tags, matching decisions, and exports when identity changes. One-off corrections remain local decisions unless the user chooses to save a general rule.

### 3.6 Collection to DJ software and USB

1. Resolve collection, app installation/version, and target device profile.
2. Check identity/quality policy, path availability, media compatibility, available capacity, and pending personal-review requirements.
3. Prepare a concrete manifest of added/changed tracks and crates; preserve existing app data.
4. Use a supported import method and verify the result by reading the target state or obtaining explicit user confirmation for assisted paths.
5. Request or guide analysis. Track whether BPM/key, waveforms, beatgrids, and cues were actually produced or preserved.
6. Export through a validated method for the selected device-library format.
7. Verify file references, contents, counts, and integrity; record which checks were automated and which require hardware.
8. Provide safe-eject guidance or a supported eject action after all handles are closed. Formatting a drive is outside normal collection/export execution.

Both Serato external-library portability and rekordbox player media are required workflows. They have separate adapters and readiness definitions.

### 3.7 Ready-now delivery and later arrivals

Ready tracks are usable while a large job continues. A 200-track request with 160 validated tracks must allow a frozen 160-track collection/export without waiting for the last peer or review item. The missing ledger remains attached to the source collection.

Default app publication occurs at a requested completion/deadline snapshot; offer explicit export-now and optional configured batch publication. Avoid writing native app libraries after every individual transfer. Later arrivals create a collection/export revision with a visible append/update plan. Preserve existing order and manual edits; additions do not silently remove earlier membership. A deadline stops new attempts according to policy and reports the ready snapshot and remaining work.

### 3.8 Illustrative human/assistant interaction

The following is the desired behavior, using invented counts:

~~~text
DJ: Get the tracks from this set. Use my Club profile and put them in
    a new rekordbox playlist. I need what is ready by 6 pm.

Tool: Started “Friday set” with your saved source and quality rules.
      I will reuse owned tracks, collect exact versions, and keep
      uncertain or lower-quality files in review. Job: job_friday.

Later status:
      27 tracks ready now: 8 owned, 19 acquired and checked.
      3 transfers still queued; 2 version choices need review.
      4 have purchase links; 2 set sections remain unidentified.
      You can import the ready collection now or wait until 6 pm.

DJ: The track at 42:10 is the dub. Import what is ready now.

Tool: Saved that correction for this set and resumed its search.
      Created export revision 1 with 27 ready tracks.
      Rekordbox is open; this import method needs it closed.
      Downloads are continuing. Say “continue the import” when ready.

DJ: Continue the import.

Tool: Imported 27 tracks and verified the playlist in the supported
      rekordbox version. Analysis is still running.
      The three late transfers remain attached to job_friday.
~~~

Exact wording should remain brief and musical. Every claim must come from persisted state/target evidence; the assistant cannot generate the last message from a successful XML write alone.

## 4. Preferences, review, and automation

### 4.1 Named profiles

Distinguish a **library workspace** (catalog, managed roots, source connections, runtime ownership) from a **preference profile** (Club/Archive/Discovery rules selected per request). Preference profiles in one workspace share assets, jobs, and the same coordinator. A separate workspace has its own catalog/runtime and cannot simultaneously claim ownership of another workspace's managed root; externally referenced roots may be shared read-only.

Provide editable built-in examples (Club, Archive, Discovery) and user-defined profiles. A profile contains:

- Catalog scope and alias policy.
- Source permissions, precedence, fallback behavior, and external-service credentials by reference.
- Version requirements; permitted substitutions default to none.
- Quality policy by codec, provenance, format, and target. Avoid one universal bitrate comparison across codecs.
- Low-quality disposition, upgrade policy, and whether audition is required.
- Metadata precedence, selection among workspace-owned registered storage roots, logical taxonomy, and destination collections. A preference profile cannot silently add a writable root or claim another workspace's managed root.
- Analysis and recognition providers, cache policy, request limits, model settings, and budgets.
- Maximum tracks, bytes, concurrent transfers, per-peer concurrency, runtime, and disk reserve.
- App/device targets, duplicate handling, and export behavior.
- Action policy: acquisition, tag writes, crate creation, replacing files, moving external originals, and recurring jobs.

A request-level override applies only to that job unless explicitly saved. Store the effective profile snapshot in the plan so later edits do not silently alter a running batch.

### 4.2 Plans and execution

Acquisition, audio/file changes, organization, app import, and device writes have structured operation plans. Automatic mode may create and execute a plan in one user action when existing policy covers the requested scope. Review mode exposes the same plan before commitment. Configuration updates, review decisions, pause/resume/cancel, and plan creation itself are validated, idempotent, audited commands; they do not require another plan/apply ceremony.

A plan records input, scope, source strategy, estimated cost/storage, permitted actions, destination, evidence revisions, unresolved questions, budget caps, and expiration/revalidation rules. Approximate discovery counts are labeled approximate.

The normal plan_collection call creates a bounded intent plan with known input, scope, policy, destination, and budgets. Discovery and candidate expansion run after start_collection within that scope. An unresolved artist identity or essential target ambiguity blocks only the dependent effect. For an itemized preview, offer a discovery-only job that returns promptly and later produces an expanded plan; do not perform a complete discography scan inside a synchronous MCP planning call.

Execution validates that authorization, target paths, selected versions, and budget still match. Material changes to destructive effects or paid scope create a new decision; ordinary new high-confidence tracks within the authorized batch do not trigger repetitive prompts.

Costs are capped before dispatch. Reserve estimated billable work transactionally across workers; reconcile actual usage. An unavailable estimate must be surfaced before exceeding the user's configured scope. No automatic storefront purchases in normal acquisition; keep exact purchase links and resume after ingesting purchased files.

### 4.3 Review queue

Each item has a stable ID, reason code, related entity/job, short explanation, evidence, choices, recommended choice when justified, and revision. Review categories include:

- Artist ambiguity.
- Original/edit/remix/version ambiguity.
- Low-quality or suspicious file.
- Conflicting metadata or probable duplicate.
- Purchase or user authentication required.
- Unsupported app/device operation.
- Proposed replacement affecting existing cues/grids.

Support grouped actions only when the same reasoning applies. A user's choice can accept, reject, choose another candidate, change a local rule, defer, or skip. A deferred item stays searchable; it does not become a fabricated success.

Make musical choices actionable: include a timestamped original-set link and concise mix-name/duration/release/source comparisons. Provide safe local play/open actions for available candidate media, initiated by the user; do not stream arbitrary peer files directly to a decoder. Include “none of these,” a supplied correction, and defer. Resolving a decision normally reschedules eligible dependent tasks within the original authorization; new cost/scope requirements become a separate decision.

### 4.4 Exceptions and recovery behavior

| Condition | Required behavior |
| --- | --- |
| Provider is unavailable | Continue independent work; show provider state and retry information. |
| Peer is queued or offline | Persist transfer identity and queue state; retry/fallback within limits. |
| Recognition finds no match | Keep timestamped unknown evidence; offer another provider, a local hint, or later rescan. |
| Only a wrong version is available | Retain candidate evidence; require a substitution decision. |
| Download is truncated or incorrect | Quarantine it, mark validation failure, and consider the next eligible candidate. |
| Disk reserve would be breached | Stop scheduling new writes; checkpoint; report required space. |
| User cancels | Stop new work, request cancellation from providers, reconcile late completions, and report retained files. |
| Process/laptop restarts | Recover leases and reconcile external jobs before resubmission. |
| External library changed | Revalidate affected mutations; preserve external edits. |
| USB disappears | Checkpoint export and require the same volume identity before resuming. |
| Target app/version is unsupported | Provide a clearly labeled prepared artifact or assisted path; retain native automation as unavailable. |
| API budget runs out | Stop billable work; continue eligible local work; report spend and remaining requests. |

## 5. Music identity, metadata, and organization

### 5.1 Canonical entities

| Entity | Responsibility and important fields |
| --- | --- |
| Artist | Local ID, credited names, aliases, external IDs, relationships, evidence. |
| Recording | A specific performed/mixed version; normalized identity, version markers, duration evidence, artist credits, optional external IDs. |
| Work relationship | Optional composition/underlying-song relationship; useful for remixes and covers without forcing them into one recording. |
| Release | Album/EP/single/compilation edition, catalog number, label, date, external IDs. |
| Release track | Recording appearance, disc/track position, credited version/title, edition/mastering evidence. |
| Source candidate | Provider location, offered metadata/quality, rights/access hints, availability, ranking evidence, expiry. |
| Media asset | Stable logical original/derived media object, recording association/confidence, provenance, parent derivative, preferred-target selection. |
| Asset revision | Exact observed byte revision, checksum, tag snapshot, measured properties, optional audio-payload identity, preceding revision, validation. |
| File location | Asset path/URI, volume identity, ownership (managed/external), size/mtime observations, availability. |
| Set and occurrence | Source set, timing interval, overlapping/repeated candidate recordings, supporting evidence, recognition revisions. |
| Collection and membership | Static, smart, source-derived; track order, local notes, accepted version policy. |
| Tag assertion | Field/value, source, confidence, user override, analysis/model version, decision history. |
| Job and operation | Intent, plan revision, action policy, task graph, progress, events, budget, results. |
| Review item | Typed uncertainty and permitted resolutions with optimistic-concurrency revision. |
| App binding and export | External app IDs, paths, snapshots, applied changes, manifest, device target, verification level. |

Use local stable IDs even when external catalog IDs are absent. Unique external IDs are namespaced by provider and entity type. Treat ISRC and catalog matches as evidence; they do not prove every edit/master is interchangeable. Preserve distinctions in release/mastering evidence when catalogs collapse them.

### 5.2 Matching and deduplication

Resolve in stages:

1. Normalize punctuation, Unicode, artist separators, and common naming noise without stripping meaningful mix/edit qualifiers.
2. Generate candidates using names, IDs, release context, duration, and credited artist relationships.
3. Apply hard constraints for requested versions and incompatible durations; tolerate known encoding offsets where justified.
4. Rank with explainable feature evidence; calibrate automatic acceptance against labeled examples.
5. Compare downloaded audio with expected identity where a reliable reference/fingerprint exists.
6. Deduplicate exact bytes by cryptographic hash. Use audio fingerprints and metadata for probable equivalence across encodings; retain uncertainty around edits and masters.
7. Keep multiple assets where quality/provenance/mastering meaningfully differs. Choose a preferred asset per target/profile.

Fingerprint similarity is supporting evidence, not a universal proof of exact mastering or source quality. Human corrections are retained as labeled examples for evaluation; they do not automatically train or upload a model.

### 5.3 Audio validation and quality

Required: valid container/codec, plausible duration and channel count, completed transfer, decode checks, expected identity/version evidence, measured size/bitrate/sample rate/bit depth where applicable, and target compatibility.

Optional analyses: silence/truncation, unusual clipping, spectrum-based quality warnings, BPM/key, audio embeddings, mood/energy suggestions. Store model/version and uncertainty. Spectral heuristics cannot certify an original lossless source.

Keep streaming-derived provenance visible. Re-encoding into a larger file is not a quality upgrade. Preserve originals and generate compatibility derivatives only when the chosen target requires them. Do not normalize loudness, trim silence, or alter audio destructively as part of routine import.

### 5.4 Storage and metadata rules

Managed storage uses stable asset identity; a readable artist/release/title layout is a presentation layer with a persisted path manifest. Do not rename exported/imported media implicitly when later metadata improves.

An asset's identity remains stable while a tag write creates a new byte revision at its managed path. Native DJ apps may legitimately change embedded tags and therefore full-file hashes. Preserve prior revision evidence and reconcile new observations; hash changes alone do not imply a new recording or corrupt audio.

Default preservation means retaining original audio content, preserving external originals, and keeping tag snapshots/journals for managed changes. It does not mean keeping an untouched byte-for-byte duplicate of every newly downloaded file indefinitely. Archive mode can retain source blobs explicitly, and storage reservations then include that extra copy. Exact pre-tag file restoration is guaranteed only when a retained source/revision copy exists; otherwise undo restores supported metadata/owned changes and reports its limits. Compatibility derivatives retain their source asset; never hardlink mutable copies to archived originals.

- External originals remain in place unless an explicit operation adopts/moves them.
- Save original tag snapshots before managed mutations; preserve unknown/native DJ tag blocks.
- Store private app state and complete provenance in the catalog/sidecars; export a supported subset of tags to audio.
- Define tag mappings for ID3, Vorbis comments, MP4 atoms, and WAV/AIFF limitations per format.
- User overrides outrank later automated metadata; allow field-level unlocking.
- Existing cue points, loops, grids, ratings, and play history require explicit preservation checks.
- Do not hardlink mutable working copies to an external original.
- Native library references track exact assets and paths; replacing an asset can require new analysis or explicit cue/grid migration.

### 5.5 Collections and musical understanding

Provide static crates, smart collections (saved typed queries), and source collections tied to a set, artist, or acquisition job. A track can have multiple genres/moods; the physical file need not move.

Initial organization uses metadata and user rules. Later audio analysis supports BPM/key and editable energy/mood suggestions. A language model can interpret “darker UKG suitable for warm-up” into a query and explain suggestions; audio-derived assertions require actual audio analysis or explicit user evidence.

Keep canonical musical key notation and export-specific display mappings separate. Detect common half/double-tempo ambiguity. Final beatgrids remain target-app analysis unless independently validated. Smart collections must distinguish live membership from an exported frozen snapshot.

## 6. Architecture and local execution

### 6.1 Application shape

Use a Python modular monolith with a local coordinator, bounded background work, SQLite, and adapter modules. The same typed application services power every interface. Most complexity belongs in domain rules and reliable integration, not a distributed deployment.

~~~mermaid
flowchart TD
    CLI[Human and JSON CLI] --> API[Local application service]
    MCP[Local MCP server] --> API
    CHAT[Conversational CLI] --> API
    WEB[Future local web UI] --> API
    API --> POLICY[Plans, policies, and capabilities]
    POLICY --> COORD[Coordinator and durable jobs]
    COORD --> DB[(Local SQLite catalog)]
    COORD --> DISC[Set and catalog discovery]
    COORD --> SOURCES[Source adapters]
    SOURCES --> STAGE[Staging and validation]
    STAGE --> LIB[Managed assets and collections]
    LIB --> EXPORT[App and device adapters]
    EXPORT --> TARGET[Serato, rekordbox, selected media]
~~~

### 6.2 Recommended stack and boundaries

| Concern | Baseline choice | Reason / boundary |
| --- | --- | --- |
| Language/runtime | Supported Python release selected in M0, initially targeting Python 3.12+ subject to dependency verification | Strong metadata/audio ecosystem; distribute one core library. |
| Dependency/release tooling | pyproject.toml and uv lock/build workflow | Reproducible contributor setup; pin integration versions. |
| Typed contracts | Pydantic and JSON Schema | Validate CLI/MCP/HTTP inputs and publish schemas. |
| CLI | Typer and Rich | Human output with a separate stable JSON serializer. |
| Local transport | Small authenticated loopback HTTP API using FastAPI; internal service calls stay transport-independent | Portable across macOS/Windows; shared by CLI, MCP, and later local web. |
| Persistence | SQLAlchemy, Alembic migrations, SQLite on local disk | Versioned schema and transactional job/catalog state. |
| Concurrency | One coordinator, bounded async provider work, process/subprocess workers for audio tasks | Avoid blocking job/status operations during audio processing. |
| HTTP | httpx with common timeout/retry/rate policies | Keep provider-specific behavior inside adapters. |
| Audio | FFmpeg/ffprobe, Mutagen, optional Chromaprint | Reuse existing media components. |
| Soulseek | slskd as separately installed/configured service | Reuse network and transfer behavior. |
| Extraction | yt-dlp with its verified runtime requirements | Keep extractor failures isolated and upgradeable. |
| Recognition | AudD and/or ACRCloud adapters | Bring-your-own credentials and budgeted use. |
| Agent tools | Official supported MCP Python SDK selected/pinned in M0 | Avoid hand-implementing transport/protocol details. |
| Model providers | Small adapter interface with schema-constrained command translation | Host-provided LLM via MCP/skill; optional BYO model for standalone chat. |
| Later web | Server-rendered templates, plain HTML/CSS, minimal enhancement | Same engine; small maintainable surface. |

Supporting projects: [Typer](https://typer.tiangolo.com/), [Mutagen](https://mutagen.readthedocs.io/en/latest/index.html), [beets](https://docs.beets.io/en/latest/), [Chromaprint](https://acoustid.org/chromaprint). Evaluate beets as a component/reference for metadata matching; keep one authoritative application catalog and avoid introducing a competing library owner.

### 6.3 Process lifecycle

- Commands requiring application state ensure a single per-user/per-library-workspace local coordinator is running, or connect to an explicitly configured instance. Preference profiles share this coordinator. Help, version, bundled schemas, basic capabilities, and diagnostic doctor checks remain available when the service/config/migrations are broken.
- Protect startup with a lock. Store port, instance ID, protocol version, and an access-token reference in a private runtime directory. Validate stale process records before reuse.
- The coordinator owns mutable application state. CLI and MCP clients never edit database tables directly.
- Long jobs live independently of the client process. Closing an LLM session or terminal does not delete/cancel its accepted job.
- Provide service start/status/stop and service run --foreground commands for debugging and headless deployment; these launch/control the same coordinator. Stop checkpoints unfinished work; an optional drain mode finishes active work first. Optional startup-at-login is an explicit setting.
- Idle shutdown is permitted only when there are no active jobs, external transfers, scheduled retries/polls, or enabled scheduling obligations. Explicitly paused jobs and jobs solely awaiting user input may quiesce. Waiting peers and future retries keep the scheduler available.
- Model calls and media analysis run outside the event loop's critical sections. Cancellation reaches subprocesses/provider jobs and has timeouts.
- Runtime and database directories live on a local filesystem. Audio assets may live on external drives with availability tracked independently.

Run one Uvicorn process with production reload/multi-worker mode disabled. A lifetime OS lock owns the library workspace; migrations finish before requests are accepted. A serialized writer queue owns short database mutations, with a fresh SQLAlchemy session per unit of work. CPU/media subprocesses return results/artifacts and never write the catalog. Only the coordinator validates task generations and commits results.

The precise launch mechanism and shutdown behavior must be proven on macOS and Windows in M0/M1, including actual assistant-host closure and Windows process/job-object behavior. Jobs survive disconnect; after logout/reboot, work resumes when the service next starts unless optional login startup is enabled. Native desktop DJ integrations are not promised on Linux; headless acquisition, analysis, CLI, MCP, and export generation can be supported there.

### 6.4 Persistence and schemas

Use foreign keys, uniqueness constraints, schema migrations, parameterized queries, and short write transactions. One logical writer serializes state changes; concurrent work returns results through it. Never hold a transaction open during a network transfer, model call, or FFmpeg execution.

If WAL mode is selected, verify the actual bundled SQLite runtime against supported patched versions, require local-disk storage, and use the SQLite backup API/safe checkpoint procedure. Copying a live database file alone is not a backup strategy. Maintain migration rollback/restore documentation and refuse unsupported schema downgrades. [SQLite WAL guidance](https://sqlite.org/wal.html).

Index external IDs, normalized names, asset hashes, active tasks, due retries, memberships, and job events. FTS supports catalog text search initially. Introduce audio vector search only when a measured use case requires it; store model/version alongside embeddings.

Large raw provider responses, recognition evidence, waveforms, and audio stay in bounded files or content-addressed artifact storage, with checksums referenced from the database. Apply retention limits to downloaded analysis audio and temporary data. Durable user decisions and provenance outlive disposable caches.

### 6.5 Proposed repository structure

~~~text
dj-library-tool/
  PLAN.md
  README.md
  pyproject.toml
  uv.lock
  src/djlib/
    domain/          # identities, policies, plans, collections, errors
    application/     # shared use cases and permission checks
    persistence/     # models, repositories, migrations, journals
    jobs/            # coordinator, tasks, leases, recovery, events
    providers/       # catalogs, sources, extractors, recognition
    audio/           # probing, validation, fingerprints, metadata
    integrations/    # rekordbox, Serato, device profiles
    interfaces/      # CLI, local API, MCP, conversational CLI
    web/             # added in the later web milestone
  integrations/
    skills/dj-library/SKILL.md
    codex/           # documented installation/config examples
    claude-code/     # documented installation/config examples
  schemas/           # generated versioned public contracts
  tests/             # domain, contract, recovery, integration
  fixtures/          # licensed demo media and recorded responses
  evals/             # labeled music/agent scenarios and reports
  docs/
    architecture/
    decisions/
    compatibility/
    providers/
    security/
  site/              # later public website source
  .github/           # CI, release, issues, contributor templates
~~~

Directory names may evolve; domain services and public contracts should not depend on an individual model provider or interface.

## 7. Durable jobs, idempotency, and recovery

### 7.1 Execution graph

A collection is a persisted graph of item tasks: discover, resolve, search_owned, search_sources, choose_candidate, transfer, validate, tag, promote, organize, and export. Discovery may add items incrementally. Persist the graph and plan before dispatching effects.

Job execution states: queued, running, paused, needs_attention, completed, failed, cancelled. A completed job has an outcome of complete or completed_with_gaps; outcome is separate from execution state and from app/device readiness.

Task states: pending, ready, running, retry_wait, waiting_external, needs_input, succeeded, skipped, failed, cancelled. A job enters needs_attention only when there is no independent runnable work and a decision is needed. External waits have deadlines and visible reasons; they do not imply failure or success.

Each task records attempts, worker lease, lease generation, heartbeat, operation ID, input revision, provider correlation IDs, next retry time, and result/error. A stale worker cannot commit after its lease generation has been replaced.

Pause stops scheduling new tasks; already active transfers may finish into staging unless the provider supports pausing. Cancel stops new work and requests provider/subprocess cancellation. Completed library effects remain until a separate undo operation. Report late remote completions and quarantine them if the cancelled job no longer authorizes ingestion.

### 7.2 Idempotency contract

- A client mutation supplies an idempotency key. The server stores the key, semantic request hash (including effective preference profile/plan), caller/workspace scope, operation ID, and result.
- Reusing the same key and same request returns the existing operation; reusing it with different parameters returns a conflict.
- Retries of one operation do not acquire duplicate assets or append duplicate crate entries.
- A new collection request may intentionally refresh discovery, but reuses eligible existing assets.
- Local uniqueness constraints protect asset identity, item membership, and external bindings; ordered set occurrences remain independently representable.
- External APIs may not provide exactly-once semantics. Persist intent, correlate effects, and reconcile uncertainty before resubmitting.
- Candidate identity and chosen asset are immutable for a committed task revision; a correction starts an explicit revision.

Human CLI commands generate a new key for each new intent and persist/return it; transport retries automatically reuse that key. A caller can provide a key to resume an uncertain submission. MCP mutations require or expose a stable operation key according to their schema and return it. A fresh command invocation is a new intent, while library uniqueness still prevents duplicate accepted assets. Retain keys at least as long as the associated job/receipt retention window and document behavior after expiry.

### 7.3 Filesystem operation journal

Database state and filesystem effects are not one atomic transaction. Implement a journaled protocol:

1. Persist the planned operation, expected destination, input hashes, and ownership.
2. Transfer into an operation-specific incomplete directory.
3. Confirm remote completion; probe/decode/identify the local result.
4. Tag a staged managed copy; preserve required existing metadata; compute final hashes.
5. Stage on the destination filesystem if crossing volumes.
6. Persist intent to promote, then atomically rename into the managed path where supported. Refuse an unexpected conflicting file.
7. Commit asset locations, collection references, and receipt; mark operation complete.
8. Recovery inspects incomplete journal entries and reconciles staging/final files by hashes before completing or rolling back its owned changes.

Never overwrite an external original or unrelated destination as collision handling. Temporary copies have retention policies. If app-managed tags alter a file later, record a new asset observation/revision and avoid treating it as corruption or an automatic replacement trigger.

### 7.4 Remote transfer recovery

Record provider transfer IDs immediately when received. If a request times out after possible remote acceptance, query existing transfers using a correlation key or remote file/peer identity before retrying. If correlation is ambiguous, expose an uncertain transfer state and reconcile instead of creating multiple downloads.

In slskd, service paths may differ from host paths. Store validated path mappings and resolve only completed files beneath configured roots. Provider-owned partial files remain under provider ownership; the application takes ownership of an ingested copy only after validation.

Persist sufficient candidate, search, and transfer evidence in our own catalog: provider history may expire or be removed, and volatile service state may disappear at restart. If a completed file is removed before ingestion or correlation history is lost, record that gap and reconcile surviving paths/hashes before scheduling another transfer. Include retention-loss and provider-reset cases in contract tests.

### 7.5 Retry and backpressure

Use provider-specific retry classification, Retry-After, exponential backoff with jitter, bounded attempts, and deadlines. Auth failures suspend affected provider work until repaired. Rate-limit and circuit-breaker state is shared across jobs, not reset for every track.

Schedule bounded network transfers, per-peer work, audio CPU work, and model/recognition calls independently. Track disk reservations and release them on failure. Queue length must not allocate all audio or full provider results in memory.

### 7.6 Undo and reconciliation

Undo operates on a changeset, with preconditions that detect intervening external changes. Reverse owned crate additions, restore prior managed tag snapshots when safe, and remove only unreferenced owned artifacts according to retention policy. Never promise universal rollback of arbitrary live DJ application state.

Support rescan/reconcile commands for missing volumes, moved files, external tag edits, target-app changes, and partially applied exports. A repair plan must state its evidence and effects. Export receipts are immutable snapshots; later collection changes create new exports.

## 8. Exemplary CLI and agent interfaces

### 8.1 Interface principles

The public command/API contract is a product deliverable. Human CLI, JSON CLI, MCP, standalone chat, and local web call the same use cases and policy layer. No interface has hidden privileged write behavior.

Every long action returns a persistent job/operation ID quickly. “Accepted” means queued or started, not completed. Results include the current stage, known counts, unresolved reasons, and an appropriate next action.

Keep tools task-oriented and descriptions short. Agents should not need to reconstruct hundreds of low-level file actions. Input/output schemas are generated from shared models, checked into versioned documentation, and compatibility-tested.

### 8.2 Proposed CLI surface

Commands below specify intended behavior; they are not available yet.

Global workspace selection resolves a saved library workspace by ID/path; commands then choose an optional preference profile within it. MCP server setup binds an explicit workspace or the user's configured default. Responses identify the workspace and effective profile without leaking secrets. Do not use the current shell directory to guess a writable music root.

~~~text
djlib init
djlib doctor --json
djlib capabilities --json
djlib schema collection-request --json
djlib demo --workspace PATH

djlib library scan PATH --json
djlib library search --query QUERY --limit 20 --json
djlib library reconcile --json
djlib sources list --json
djlib sources configure PROVIDER
djlib profiles show PROFILE --json

djlib sets inspect URL --profile PROFILE --json
djlib collect set URL --profile PROFILE --json
djlib collect artist ARTIST --scope originals,remixes,collaborations --json
djlib collect list TRACKLIST_FILE --profile PROFILE --json
djlib organize PATH --profile PROFILE --dry-run --json

djlib plans show PLAN_ID --json
djlib plans apply PLAN_ID --revision REVISION --idempotency-key KEY --json
djlib jobs list --json
djlib jobs show JOB_ID --json
djlib jobs wait JOB_ID --timeout SECONDS --json
djlib jobs items JOB_ID --status needs_input --limit 20 --json
djlib jobs events JOB_ID --after CURSOR --follow --format ndjson
djlib jobs pause JOB_ID --json
djlib jobs resume JOB_ID --json
djlib jobs cancel JOB_ID --json
djlib jobs retry JOB_ID --only missing --json

djlib review list --job JOB_ID --json
djlib review resolve REVIEW_ID --choice CHOICE_ID --revision REVISION --json
djlib collections show COLLECTION_ID --json
djlib crates update CRATE_ID --request-file CHANGESET_JSON --json
djlib exports plan COLLECTION_ID --target TARGET_PROFILE --json
djlib exports apply EXPORT_PLAN_ID --revision REVISION --json
djlib exports verify EXPORT_ID --json
djlib changes undo CHANGESET_ID --dry-run --json

djlib service start
djlib service status --json
djlib service stop
djlib service run --foreground
djlib mcp serve
djlib chat --profile PROFILE
djlib web
~~~

All mutating commands support the shared idempotency policy, even where the example omits the flag. Noninteractive commands support a request file/stdin for complex structured input. Human convenience commands may create/apply a plan within saved policy. Dry-run does not download, tag, move, or export audio; provider inspection may perform documented network reads and cache metadata, with billable recognition requiring its own explicit operation/budget.

### 8.3 Machine-readable behavior

- JSON mode emits exactly one result envelope to stdout. Diagnostics/progress go to stderr; no banners, prompts, ANSI sequences, or extra prose enter stdout.
- NDJSON is a distinct event mode with one complete JSON object per line and monotonic event cursors.
- Non-TTY usage implies no interactive prompts; an explicit no-input flag also disables them in a terminal.
- Lists have limits, pagination cursors, and optional field selection. Default agent summaries stay compact, with full evidence accessible by IDs/artifacts.
- Dates use UTC ISO 8601; durations declare units; sizes use bytes; unknown values are null with reason codes where useful.
- Commands describe compatible schema versions and supported features. Additive fields are allowed within a schema major version; breaking semantics need a new major version and migration window.
- A locally accepted job exits successfully. A wait command may return a distinct completed-with-gaps or attention-needed exit code; document this separately from enqueue success.
- A jobs wait timeout returns the latest nonterminal snapshot with timed_out=true and exit code 9. It does not cancel or pause the underlying job. Terminal failure, gaps, and a user decision have their own outcomes/codes.
- Signals detach/cancel according to the documented command mode. Exiting a status watcher does not cancel the job.

Proposed envelope:

~~~json
{
  "schema_version": "1",
  "ok": true,
  "request_id": "req_example",
  "result": {
    "job_id": "job_example",
    "state": "running",
    "outcome": null,
    "stage": "acquisition",
    "counts": {
      "identified_recordings": 40,
      "owned_reused": 12,
      "acquired_validated": 18,
      "purchase_required": 6,
      "identity_review": 4
    },
    "next_poll_after_seconds": 15
  },
  "warnings": [],
  "error": null,
  "next_actions": [
    {"action": "get_job", "arguments": {"job_id": "job_example"}}
  ]
}
~~~

The example is illustrative. Counts have documented units and whether they are disjoint; sum totals only where the schema says categories partition a population. A running job's counts may change as discovery adds items.

Error envelope fields: code, message, retryable, affected IDs, scope, retry_after_seconds, details_reference, and permitted next_actions. Stable examples: ARTIST_AMBIGUOUS, VERSION_UNCERTAIN, PROVIDER_AUTH_REQUIRED, SOURCE_UNAVAILABLE, BUDGET_EXHAUSTED, DISK_RESERVE_REACHED, PLAN_STALE, IDEMPOTENCY_CONFLICT, TARGET_APP_RUNNING, TARGET_VERSION_UNSUPPORTED, DEVICE_DISCONNECTED.

Proposed CLI exit codes: 0 accepted/success; 2 invalid input; 3 input/decision required; 4 provider/auth/dependency unavailable; 5 action denied by effective policy; 6 operation failure; 7 waited job completed with gaps; 8 stale-plan/conflict; 9 wait timeout with job still active; 130 interrupted foreground command. Freeze and test these in M1 before public use.

### 8.4 MCP tool design

Use local stdio as the initial distribution path. The server is a thin authenticated client of the coordinator and uses an official supported SDK. Keep stdout protocol-only and logging on stderr. Do not depend on optional/experimental long-running-task or elicitation features to complete a workflow; persisted jobs and structured review records work across hosts. [MCP tools](https://modelcontextprotocol.io/specification/latest/server/tools).

Keep the application JSON schema version separate from the MCP protocol version. Let the selected SDK negotiate supported protocol versions, and record actual host/SDK/protocol combinations in the compatibility matrix. A newly published protocol feature is not automatically available in both supported hosts.

Proposed tool set:

| Tool | Purpose | Side-effect class |
| --- | --- | --- |
| get_capabilities | Supported providers, versions, budgets, targets, setup status | Read |
| get_profile | Bounded effective preferences with secret values excluded | Read |
| get_plan | Bounded plan, revision, scope, effects, and decision requirements | Read |
| search_library | Bounded owned/catalog search | Read |
| get_collection | Membership, status, and provenance summary | Read |
| plan_collection | Create a bounded persisted intent plan; return a job for requested detailed discovery | Local planning write; documented external reads |
| start_collection | Execute a plan within effective policy and return a job | Acquisition/library effects |
| inspect_set | Start a metadata/recognition analysis job with explicit budget | Analysis/network effects |
| get_job | Progress, stage, outcome, and next poll time | Read |
| list_job_items | Paginated evidence, failures, reviews, and missing items | Read |
| resolve_item | Apply a versioned choice to a review item | Decision write; rescheduling described explicitly |
| control_job | Pause, resume, retry, or cancel | Job effects |
| update_crate | Apply scoped membership/order changes | Library effects |
| plan_export | Inspect target and create an export plan | Local planning write |
| execute_export | Apply an export plan with target preconditions | App/device effects |
| verify_export | Start potentially lengthy readback/hash verification and return a job | Local job/receipt write; target read-only; no implicit repair |

For each tool publish inputSchema, outputSchema, truthful behavior annotations, limits, idempotency semantics, and two representative examples. Planning calls that persist state are not mislabeled read-only. Annotations assist hosts but do not replace server policy checks. Resource URIs may expose detailed reports; tools must also provide a compatible paginated path for hosts that do not surface resources well.

Tool errors use structured application errors and the SDK's appropriate error indicator. Malformed protocol requests remain protocol errors. Do not expose generic shell, raw SQL, unrestricted fetch, or unrestricted filesystem-write tools.

### 8.5 Skill and supported LLM CLIs

Ship a portable Agent Skills folder with a concise SKILL.md and deeper references/examples. It teaches the normal workflow, matching judgment, recovery, source policy, and truthful reporting. It calls public CLI/MCP operations; it does not edit the database or invent new flags. [Agent Skills specification](https://agentskills.io/specification).

First supported host matrix: Codex CLI and Claude Code, with a generic standards-based MCP setup for others. Verify actual installation and a complete workflow in both; do not equate standards compatibility with tested host support. Codex supports local stdio servers and documents CLI/configuration setup. Claude Code separately documents its MCP setup. [Codex MCP](https://learn.chatgpt.com/docs/extend/mcp), [Claude Code MCP](https://code.claude.com/docs/en/mcp).

Manual Codex registration after a persistent installation (use the generated launcher for the normal session path):

~~~sh
codex mcp add djlib -- /absolute/path/to/djlib --workspace /absolute/path/library mcp serve
~~~

The executable and complete skill are packaged. On Windows, first start the coordinator from an external terminal with `djlib --workspace PATH service start`; generated `launch.py` starts it before the AI host. Windows MCP cold start rejects with `COORDINATOR_START_REQUIRED` rather than escaping the host's Job Object. At each release verify current host commands and skill locations; use [agent setup](docs/AGENTS.md) for maintained instructions. [Codex skills](https://learn.chatgpt.com/docs/customization/overview#skills).

Skill workflow:

1. Read capabilities and relevant profile; resolve only consequential ambiguity.
2. Plan and execute within the user's saved/requested scope.
3. Save job/collection references in the conversation; poll at the server's suggested interval.
4. Summarize progress compactly and group necessary decisions.
5. Apply explicit corrections with entity IDs and revisions.
6. Report acquired, unresolved, app-imported, analyzed, and device-exported outcomes accurately.

MCP/skill users bring their host assistant; they do not need a second model account inside djlib. A future plugin bundle may package the skill and MCP configuration after the portable interfaces are stable. Automatic edits to a user's host configuration must be explicit setup actions with a preview and backup.

### 8.6 Standalone conversational CLI

Provide a chat command using a configurable model provider. Start with one documented cloud adapter and one local-model adapter only after their required tool/schema behavior is verified. Do not hardcode a model brand into the domain.

The chat loop stores session IDs and referenced jobs/collections, uses typed tool calls, enforces action policy and token/cost limits, and supports interruption/resume. Conversation text is not the source of truth for job progress. Model failure does not stop already accepted background jobs.

Interpret scope, explain choices, translate musical queries, and summarize reports. Do not use a text model's memory as authoritative discography, download availability, or audio recognition. Validate all model output as untrusted input to the same services used by CLI/MCP.

## 9. Provider contracts and source integrations

### 9.1 Small capability interfaces

Define narrow interfaces rather than requiring every source to implement everything:

| Interface | Representative operations |
| --- | --- |
| CatalogProvider | search_artist, get_artist, enumerate_releases, get_release, get_recording, get_relationships |
| SourceDiscoveryProvider | search_source_links, inspect_page, resolve_download_or_store_candidates |
| SetMetadataProvider | supports_url, inspect_source, fetch_tracklist_evidence |
| MediaExtractor | inspect_formats, estimate_transfer, fetch_analysis_audio, fetch_permitted_media |
| RecognitionProvider | capabilities, estimate_cost, recognize_segment/recording, normalize_observations |
| AcquisitionProvider | search, inspect_candidate, start_transfer, poll, cancel, resolve_completed_artifact |
| StoreLinkProvider | search_release, resolve_purchase_link; purchase completion remains a separate workflow |
| AudioAnalyzer | probe, decode_check, fingerprint, optional_features |
| DJLibraryAdapter | capabilities, snapshot, plan_import, apply_import, reconcile_receipt, analyze, plan_export |
| ModelProvider | capabilities, structured_intent, tool_turn, summarize |

Every provider reports version, health, auth state, capabilities, limits, privacy/data transfer, and diagnostic repairs. Search results carry provenance and access status: downloadable, purchase_required, authorization_required, unavailable, not_found, unsupported. One result may expose several access paths without pretending each is downloadable.

### 9.2 Catalog discovery

Use MusicBrainz as an initial catalog/identity source and artist/label/store pages as corroborating evidence. Evaluate Discogs as an optional complementary source with its own API access and data-use constraints. Neither source establishes universal completeness. Cache pagination results and negative results with bounded TTLs, provenance, and source-specific rate handling. MusicBrainz documents rate limits that require coordinated client behavior. [MusicBrainz API](https://musicbrainz.org/doc/MusicBrainz_API), [rate limiting](https://musicbrainz.org/doc/MusicBrainz_API/Rate_Limiting).

For artist catalog enumeration, persist a cursor/checkpoint, source coverage, release grouping decisions, exclusions, and fetch time. Alias/credit traversal has explicit depth/scope limits. Do not recursively collect every collaborator's catalog.

### 9.3 Direct downloads and stores

Implement actual online source discovery as well as downloading supplied URLs. A SourceDiscoveryProvider uses configured search APIs and supported artist/label/store site searches to find candidate release/download pages. Resolve discovered pages through site-specific adapters into original-download, store, authentication, or unsupported outcomes. Preserve canonical URLs, query provenance, observed availability, and exact recording/version evidence; never treat a search snippet as proof of a downloadable file.

Select the initial searchable sites/search service in M0 based on access, cost, and API stability. Search is bounded by per-recording queries, batch budgets, timeouts, and caching; report which sources were searched and which were unavailable. User-supplied source URLs and local purchased files remain supported when web search is not configured. Future source providers can extend coverage without changing collection execution.

Support direct artist/label file URLs, user-supplied files, purchased download folders, and source-specific original-download paths. Store authentication and expiring URL behavior behind adapters. A store result can be valuable as an exact release/purchase link even without an automated download API.

SoundCloud documents uploader-enabled downloads of original uploaded files. Treat enabled original downloads separately from streaming access and from offline listening inside a subscription app. [SoundCloud downloads](https://help.soundcloud.com/hc/en-us/articles/115003448787-Downloading-tracks).

Support approved archive formats with size/entry/path limits for purchased album ZIPs. Keep artwork and liner notes associated with the release; only validated supported audio becomes a playable asset. Artist/store pages requiring unsupported login, captcha, or interaction produce actionable handoff states.

### 9.4 Soulseek through slskd

Primary adapter: slskd. It provides persistent search, peer browsing, transfers, queues, cancellation, and a configurable API/OpenAPI surface. Use its supported API key mechanism and pin a tested server range. Prefer a small typed httpx adapter or a maintained client wrapped behind our interface. [slskd](https://github.com/slskd/slskd), [configuration](https://github.com/slskd/slskd/blob/master/docs/config.md).

Persist search IDs, peer IDs, remote paths, claimed media properties, candidate observations, transfer IDs, queue state, and host/container path mapping. Claimed filename/bitrate is untrusted until the completed media is validated.

Policy controls search timeout, queue patience, alternate peers, per-peer concurrency, accepted formats, exact-version constraints, and bandwidth limits. Peer browsing is bounded and explicit. The user chooses sharing roots during source setup; connecting the provider never silently shares the managed library or home directory. Detect conflicting service/account/session configurations and provide clear repair steps.

Sockseek, formerly slsk-batchdl/sldl, provides overlapping batch functionality and a newer daemon API. Its API is labeled experimental. Use it as a benchmark/research lead and an optional future provider; keep one primary Soulseek installation path and keep matching policy in our application. [Sockseek](https://github.com/fiso64/sockseek), [API status](https://github.com/fiso64/sockseek/blob/master/docs/api.md).

### 9.5 YouTube, SoundCloud, and other extractors

Wrap yt-dlp through a structured adapter with explicit staging paths, bounded output, progress translation, and dependency diagnostics. Prefer retaining native audio encoding when appropriate. Keep providers/extractors versioned and independently repairable; support for a site is exercised, not inferred indefinitely from an extractor list. [yt-dlp](https://github.com/yt-dlp/yt-dlp).

Current YouTube extraction has JavaScript runtime and EJS requirements. M0 must test the selected yt-dlp distribution, compatible yt-dlp-ejs components, supported runtime (official guidance recommends Deno), and FFmpeg/ffprobe on target OSes. Do not promise an install needing only Python. Browser cookies are opt-in credentials and must never appear in logs or model context. [yt-dlp EJS setup](https://github.com/yt-dlp/yt-dlp/wiki/EJS).

Separate extracting a set for identification from acquiring standalone tracks. A cropped section of a DJ mix cannot satisfy acquisition of the original recording. Record acquisition kind (original_download, purchased_file, user_supplied, permitted_extraction, peer_transfer), source identity, and retrieval time.

### 9.6 Recognition and timeline construction

Implement a provider-neutral recognition observation containing input segment start/end, matched track position if supplied, artist/title/external IDs, raw provider score, normalized evidence, and provider version. Do not interpret provider scores as calibrated probabilities without evaluation.

AudD accepts long audio and supports bounded sampling. Its submitted-file offset and original-song timecode have different meanings; normalize them explicitly before building a timeline. ACRCloud accepts short samples or its supported fingerprints, allowing application-controlled scanning. [AudD enterprise endpoint](https://docs.audd.io/enterprise/), [ACRCloud identification](https://docs.acrcloud.com/reference/identification-api/identification-api).

Pipeline:

1. Parse textual evidence and retain provenance.
2. Estimate scan cost and choose a scan policy within budget.
3. Sample the timeline; cache observations by audio identity, offsets, parameters, and provider/model version.
4. Merge compatible observations without erasing repeats or overlaps.
5. Rescan low-confidence intervals and transitions more densely within budget.
6. Compare textual and acoustic claims; queue meaningful conflicts.
7. Separate recording identity, exact played version, and occurrence boundaries.
8. Store unresolved intervals and annotations for future providers/user corrections.

Tempo changes, overlays, talking, and short edits belong in evaluation. Optional time/pitch-normalized retries or source separation are later experiments with cost and quality evidence, not prerequisites or promises of full coverage. Chromaprint supports local near-identical recording work; it is not a drop-in replacement for a large recognition catalog in mixed audio. [Chromaprint project](https://github.com/acoustid/chromaprint).

## 10. Serato, rekordbox, and USB integration

### 10.1 Capability and compatibility records

Each adapter publishes app name, explicit tested versions/builds and schema signatures, OS, mode (prepared_artifact, assisted, native_verified, experimental), supported operations, preservation guarantees, known limitations, test date, and evidence references. Any compatible range beyond those builds needs separate justification. Unsupported versions fall back to an explicitly labeled supported handoff.

Track collection import, nesting/order, metadata, cues/grids, analysis, portable media, and device export separately. A library helper that reads a database does not establish safe write support or analysis-file generation.

Analysis readback returns separate BPM estimate, key estimate, waveform, beatgrid, and cue-preservation observations with evidence methods. A BPM metadata tag does not establish a usable beatgrid or a completed target-app analysis.

Native writes require recognized schema/version, application-closed detection where required, complete backups, preflight, a journal, and readback. If the app changes during the operation, stop and reconcile. Do not silently use UI automation as if it were a stable API; an app-automation adapter has its own OS permissions and compatibility tests.

If the app is busy or a selected method requires it closed, defer that step and expose a simple continue-import operation through the existing job controls. Do not terminate the DJ application or take over focus during an active performance. Acquisition and safe local work continue independently.

### 10.2 Rekordbox progression

1. Produce documented XML playlists and tagged media at stable final paths. Include M3U8 as a generic fallback where verified.
2. Exercise import in the selected current rekordbox version; document actual steps and reconcile collection IDs/paths.
3. Add automatic collection/playlist integration for explicitly verified versions through a native or application-mediated adapter.
4. Orchestrate or guide analysis and read back its actual state.
5. Orchestrate the target export; separately evaluate a standalone device writer if it meets the same verification standard.

The official XML interface is documented, but the developer page uses older UI terminology. Test current behavior. Use an XML library and proper file URIs; handle Unicode, escaping, nested playlists, stable IDs, and sibling-name collisions. [rekordbox XML interface](https://rekordbox.com/en/support/developer/), [XML format reference](https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf).

pyrekordbox is an unofficial research/integration component, not proof of blanket current-version compatibility or complete USB creation. Its tested versions and read/write capabilities must be evaluated in M0. Keep it behind a replaceable adapter. [pyrekordbox](https://github.com/dylanljones/pyrekordbox).

### 10.3 Serato progression

1. Support a verified file/folder import workflow with stable paths and an import manifest.
2. Investigate current crate import/write behavior in disposable libraries on selected Serato versions and both OSes.
3. Choose a verified external-crate, native, or app-mediated import adapter based on observed round trips.
4. Reconcile order, nesting, shared track references, user metadata, and analysis state.
5. Support external-drive portability with Serato-specific library semantics.

Serato documents importing files/folders and creating a crate by dragging a folder into the crates view. It references the files where they live, making path stability essential. Its current folder documentation labels Auto Import/database V2 legacy, so those are not a universal integration strategy. [Serato import](https://support.serato.com/hc/en-us/articles/223446528-Adding-files-to-the-Serato-DJ-Pro-Library), [folder contents](https://support.serato.com/hc/en-us/articles/204022904-What-is-in-the-Serato-folder).

Backups must follow the selected version's current storage model. Serato's current backup guidance includes its Library directory in addition to _Serato_ and music files; do not implement an incomplete legacy-only backup. [Serato backup/migration](https://support.serato.com/hc/en-us/articles/202305054-Backing-up-and-moving-your-library-to-a-new-computer).

Treat third-party crate writers as research leads, not established compatibility. Preserve unrelated crates, user ordering, cue/grids, tags, and play history. If a feature cannot round-trip, expose its limitation at planning time.

### 10.4 USB and player profiles

A device profile contains model(s), firmware version or unknown, supported library formats, codecs, sample rates/bit depths, filesystem constraints, path constraints, volume identity, and export strategy. Multiple models may require an intersection of media support and more than one library format.

Current rekordbox terminology calls Device Library Plus OneLibrary. Traditional Device Library and OneLibrary have distinct compatibility and conversion semantics. Do not assume a player's model alone is sufficient where firmware changes support. Keep compatibility data dated and linked to manufacturer sources. [OneLibrary FAQ](https://rekordbox.com/en/support/faq/devicelibraryplus-6/), [USB export guide](https://cdn.rekordbox.com/files/20251021171528/USB_export_guide_en_251007.pdf), [CDJ-3000 firmware history](https://www.pioneerdj.com/-/media/pioneerdj/downloads/firmwares/players/cdj-3000/cdj-3000-firmware-change-history-ver330-en.pdf).

Preflight checks:

- Correct mounted volume identity, writable state, and available capacity including temporary copies and filesystem file-size limits.
- Supported media/analysis/library formats for the chosen devices and firmware.
- Complete source files, valid paths, acceptable quality, required analysis, and stable collection snapshot.
- Existing device-library format(s), unrelated contents, overwrite/conflict policy, and export backup options.
- Conversion effects: preserve originals and create explicit derivatives when needed; never silently convert one device library over another.

For writes controlled by this tool, use temporary files, checksums, a journal, and a final manifest. For vendor/app-controlled writes, journal the request, preserve available pre-export snapshots, observe completion, and reconcile final contents; do not claim transactional control or universal rollback inside the vendor application. Resume only after confirming the same volume. Deletion of unrelated contents and drive formatting are separate explicit maintenance operations, outside normal export. Existing media/library data must remain intact unless an approved changeset says otherwise.

Verification levels: files_copied, library_written, software_readback_verified, hardware_playback_verified. Record the level actually achieved. A profile may be generally hardware-validated while a particular export has only software checks; report both separately.

### 10.5 Full automation feasibility gate

The product target includes automatic app import and minimal-intervention USB preparation. M0 tests the feasible method; M7/M8 implement it. Preferred order: documented interface, verified native adapter, then an explicit app-mediated workflow with supported automation if available.

An app-mediated adapter needs deterministic supported steps, OS-permission diagnostics, timeouts, restart/reconnect recovery, focus handling, and cancellation. It cannot depend on an arbitrary external LLM successfully clicking through the app. Exercise busy/performance states and defer intrusive actions until permitted.

If no dependable unattended path exists for a particular app/device, document the remaining user steps, deliver the assisted mode, and retain that automation item as unresolved scope. Do not close the full-product milestone or advertise unattended support by relabeling a manual handoff. A scope change requires an explicit product decision with evidence.

## 11. Later web experience and public home

### 11.1 Two surfaces with different responsibilities

The public website is a static project home. It hosts install instructions, documentation, compatibility data, examples, release links, and clearly labeled demonstration content. It does not receive visitors' Soulseek credentials or pretend to control their local files.

The working web interface is served by the installed local engine. It operates the same profiles, jobs, review items, collections, and exports as the CLI/MCP. A future remote pairing service would be a separate opt-in product/security decision; it is not required to build the requested local utility and online home.

D09 determines breadth. The recommended small local page combines request submission, job status, and a review list, linking to CLI help for advanced operations. The fuller page inventory below is available if browser operation becomes a primary interface; it is not automatically a requirement for the initial public home.

### 11.2 Early-web visual and interaction direction

Use system fonts, simple document flow, blue underlined links, compact tables, clear labels, small controls, and a restrained palette. The early-web style should stay legible and keyboard-accessible, with visible focus, adequate contrast, semantic HTML, and usable small-screen layouts. Avoid terminal ornament or retro effects that obscure long track titles and status.

Public pages: Home, Install, How it works, CLI/MCP/Skill, Supported sources, Compatibility, Demo, Docs, Releases, GitHub. The landing page should explain the benefit in one sentence and immediately offer an install/demo path.

Local pages:

| Page | Main task |
| --- | --- |
| Request | Paste a set/link/list or describe an artist request; choose a profile; show scope and submit. |
| Jobs | See progress, ready tracks, waiting reasons, budgets, and pause/resume/retry actions. |
| Review | Compare candidate versions, sources, durations, quality, and timestamped evidence; resolve individually or in valid groups. |
| Library | Search, filter, inspect provenance, audition supported local media, edit personal tags, and select assets. |
| Collections | Manage static/smart/source collections and preview export membership. |
| Export | Select app/device profile, inspect preflight and changes, run supported operations, view verification. |
| Settings | Sources, profile rules, paths, credentials by reference, dependency health, retention, budgets. |

A single request can surface immediate ready items and actionable exceptions. Large tables use server pagination. Browser refresh/reconnection restores job state; it never restarts a download by itself. Destructive actions and native app conflicts use the same policy/decision objects as CLI and MCP.

### 11.3 Implementation and hosting

Use server-rendered templates and small progressive enhancements for live job updates, audio preview, and grouped actions. Prefer a simple event stream or polling over a separate client-state architecture. The public website can be generated statically from versioned docs; choose hosting when publishing is requested and honor the selected provider.

Keep public site assets free of private paths, service keys, real peer names, and unlicensed demo audio. Demo mode is clearly identified and resettable. A working local page must check daemon connection and authorization before showing controls as enabled.

Web implementation follows the headless product and native integration milestones. This planning task produces no frontend code.

## 12. Security, privacy, and operational boundaries

These requirements protect the user's local collection and the reliability of agent-driven operation.

### 12.1 Trust boundaries

- Treat URLs, page text, comments, filenames, tags, peer names, artwork, archives, and model output as untrusted data.
- External content cannot change action policy, source allowlists, output paths, budgets, or tool instructions.
- The model receives bounded evidence and opaque candidate IDs; execution resolves those IDs through validated application state.
- Invoke media/extractor subprocesses with argument arrays and explicit working directories; never interpolate titles or URLs into a shell command.
- No generic execute-code, shell, raw-SQL, arbitrary-write, or unrestricted proxy tool is exposed through MCP.

### 12.2 Network and file access

- Limit supported URL schemes, redirects, content sizes, timeouts, and downloaded bytes. Revalidate every redirect and resolved address against the appropriate provider policy.
- Public URL fetchers cannot reach local/private metadata endpoints or bypass restrictions through DNS changes. Explicitly configured local slskd is a separate trusted connection with its own credentials.
- Validate configured roots, resolved paths, volume identities, symlink traversal, case/Unicode collisions, reserved filenames, and destination ownership.
- Archive extraction rejects traversal, escaping symlinks, excessive entries/expanded size, and unsupported payloads. Only selected validated media is ingested.
- Bound media decoding time, memory, duration, and subprocess lifetime; isolate external tools where practical. Keep supported dependencies updated with contract checks.

### 12.3 Credentials and data disclosure

Use the OS credential store where supported; config holds references. A headless environment may use explicitly supplied environment variables or a restricted secret file. Never store secrets in plan artifacts, committed config, raw diagnostic exports, CLI command history examples, or model context.

Show each service's data boundary during setup: model receives relevant request/evidence; recognition receives selected audio or supported fingerprints; Soulseek exposes configured network identity/shares; catalog APIs receive queries. Default to minimal data. Do not upload an entire local library or private audio to an LLM automatically.

No telemetry by default. Local structured logs have redaction, size/retention controls, and request/job correlation IDs. Diagnostic bundles are previewable and omit credentials, cookies, private audio, and unnecessary personal paths/peer identifiers. Users can delete cached audio/evidence and export their catalog/decisions.

### 12.4 Local service and browser

Bind the local API to loopback, require a per-user token, validate host/origin, and protect browser mutations against CSRF. Store runtime credentials with restrictive permissions. Avoid tokens in URLs/logs; browser sessions use an explicit local handshake and appropriate cookies. Stdio MCP connects as a local authorized client and does not open a public endpoint.

Protect startup from stale lock/port files and cross-workspace mix-ups. Runtime handshakes verify workspace ID, instance ID, protocol, and process start identity before use. Protocol and catalog versions are checked before writes. Remote operation is separately configured and requires transport authentication and network policy; a public website cannot auto-connect to arbitrary local services.

### 12.5 Source and distribution policy

Record access type, provenance, and purchase/authentication needs. Respect provider permissions and the user's enabled sources. A source becoming unavailable should create a recoverable missing item, not an undisclosed change in acquisition method.

Keep license/data-use review as a release task for included code, binaries, APIs, datasets, and model weights. Do not assume a source client's open-source license also licenses the music it can retrieve. Ship only owned, generated, or appropriately redistributable demo audio and retain attribution records.

## 13. Validation and evaluation strategy

Testing here is planned work for implementation. No software tests have been run as part of writing this document.

### 13.1 Deterministic tests

| Suite | Required coverage |
| --- | --- |
| Domain | Artist disambiguation, version qualifiers, scope expansion, quality policy, field precedence, collection membership, cost/disk limits. |
| Persistence | Migrations, unique constraints, request idempotency, lease expiry, stale-worker fencing, pagination, event cursors. |
| Filesystem | Unicode/case/path collisions, external volumes, cross-volume promotion, tag preservation, conflict/undo preconditions, archive limits. |
| Provider contracts | Pagination, malformed data, rate limits, auth expiry, missing/expired URLs, queued peers, partial transfers, changed schemas. |
| CLI | JSON purity, no-input behavior, exit codes, stdout/stderr separation, signals, stdin/request files, bounded output. |
| MCP | Tool schemas/annotations, result/error translation, startup, reconnect, duplicate requests, pagination, independent jobs, resource fallback. |
| Local API | Authentication, workspace isolation, correct preference-profile selection, version negotiation, origin/CSRF protections, concurrent starts and operations. |
| Metadata/audio | Valid/corrupt/truncated audio, format-specific tags, native DJ metadata retention, exact/probable duplicates, derivatives. |
| Export | Correct XML/URIs, IDs, nested order, sibling collisions, stable replay, manifest/hash consistency, unsupported target refusal. |
| Security | Prompt injection in source data, shell metacharacters, URL redirects/private addresses, traversal/symlinks, oversized media/artwork. |

Use property tests for invariants that benefit from broad input generation, such as path normalization and duplicate prevention. Golden schemas/fixtures should test meaningful behavior and compatibility rather than snapshot incidental log wording.

### 13.2 Fault injection and recovery

Exercise interruption before and after each journal boundary, including after a provider accepts a transfer but before the response is recorded, after destination promotion but before database commit, during tag writes, during export, and during schema migration recovery.

Additional cases: fill disk, unmount a device, restart slskd, expire credentials, drop network, corrupt a staged file, change external tags, open the target app mid-operation, replace a volume at the same mount path, and restart the coordinator while another MCP client connects.

Required invariant: recovery neither loses a previously valid external original nor duplicates a committed logical acquisition/crate change. In uncertain external effects, reconciliation/attention is an acceptable result; pretending the effect did not occur is not.

### 13.3 Music evaluation corpus

Build a versioned corpus with licensing/provenance manifests. Include original/extended/radio/dub/instrumental/remix/live variants; shared artist names; Unicode; collaborations/aliases; repeated compilation appearances; misleading filenames; lossy re-encodes; incomplete files; and track-duration offsets.

For sets, assemble licensed or generated mixes with annotated occurrences, overlaps, repeats, tempo changes, speech, silence, and deliberately unrecognized tracks. Include a separate private evaluation option for legitimately available real-world material without redistributing the underlying audio. Publish shareable fixtures and aggregate results with clear limitations.

Keep tuning and held-out sets separate. Retain difficult negative examples, not only successful popular tracks. Evaluate raw recognition, recording resolution, exact-version resolution, source ranking, and post-download validation independently.

Proposed initial evidence goals:

- At least 300 labeled matching cases spanning the difficult categories before auto-selection is advertised as dependable.
- Exact-version precision target of at least 99% among automatically accepted cases, with accepted count, sample uncertainty, and acceptance/coverage rate published. A system that abstains on everything does not pass.
- Zero silent wrong-version substitutions in the curated critical regression cases.
- At least 10 annotated test sets spanning different mixing conditions before broad set-identification claims; publish observed recall/coverage and timing error rather than inventing a universal threshold.
- Artist catalog fixtures covering aliases, remixes, collaborations, production-only exclusions, and repeated releases.
- Provider cost, elapsed time, and cache reuse reported alongside identification results.

These are release targets to refine after the feasibility baseline, not product guarantees or measured results. Any change to a target must be justified in the decision log.

### 13.4 Agent acceptance scenarios

Run the same user goals through direct JSON CLI, Codex MCP/skill, Claude Code MCP/skill, and standalone chat where supported:

1. Collect a set and skip owned tracks.
2. Collect an artist's originals and remixes while excluding production-only credits.
3. Correct a wrong artist and an original/extended-version mismatch.
4. Explain why a source candidate was selected.
5. Handle an unavailable peer and continue with ready tracks.
6. Resume after the assistant reconnects without resubmitting the batch.
7. Export a partial collection, then update it when more tracks arrive.
8. Resolve a quality exception without changing global policy accidentally.
9. Prepare a supported USB and honestly report any remaining app/hardware step.
10. Ignore malicious instructions embedded in a description or filename.

Measure achieved outcome, unsupported completion claims, unnecessary questions, invalid tool calls, duplicate effects, time, response size, and tool-call count. The canonical happy-path request should require one initial user instruction after configuration; legitimate exceptions are counted separately. Keep logs/evidence redacted.

### 13.5 Real app/device verification

Use disposable target libraries and a licensed fixture collection before a personal library. Validate exact app builds on supported OSes. Include nested crates, shared tracks, non-ASCII paths, external volumes, existing cue/grid data, manual edits, duplicate execution, and two app restarts.

For devices, maintain a legacy-library profile, a OneLibrary profile, and a dual-format profile where supported. Validate software readback and physical loading/playback on at least one declared device of each advertised family. Record firmware and analysis/export path. A missing hardware check is an explicit limitation and prevents a hardware-tested claim.

### 13.6 Performance and operating targets

Use a documented reference machine and corpus. Initial targets for local operations, excluding upstream network time:

- Warm job status and paginated local search: p95 under 500 ms on a 100,000-record catalog.
- Accept/enqueue a validated bounded request: under 2 seconds; heavy discovery returns a job ID.
- A 10,000-item collection job has bounded queue memory, bounded response size, and continuous status availability.
- A restart resumes runnable persisted jobs without scanning/reprocessing every previously completed audio file.
- No unbounded polling, provider retries, metadata recursion, or model context growth.

Measure memory/CPU/storage separately for coordinator and analysis subprocesses. Establish real throughput baselines before promising tracks/hour. Provider transfer speed and queue waits are reported separately from local execution time.

## 14. Packaging, documentation, and GitHub quality

### 14.1 Installation and optional dependencies

Provide one reliable Python-package installation path first, plus a developer uv workflow. Core CLI/JSON/catalog/demo should install without a model account or Docker. Soulseek, recognition, extraction, audio analysis, and chat are modular capabilities; doctor identifies missing dependencies and provides exact supported repair instructions.

Validate the selected Python and SQLite runtime, FFmpeg/ffprobe, yt-dlp/EJS/runtime combination, slskd server/API, and app versions. Native distribution may bundle dependencies only after binary provenance, platform signing, size, and licensing are addressed. Never silently download/run unpinned binaries during a routine collection request.

macOS and Windows are primary personal-DJ platforms; core CI also covers Linux. Publish feature-by-platform compatibility. Optional native installers/package-manager recipes follow clean-machine proof; the project should not promise unattended desktop app support on platforms where the apps do not run.

### 14.2 Public repository deliverables

- README with the user benefit, a short real workflow, install/demo commands, screenshots or terminal recording, compatibility, and limitations.
- Architecture diagrams and decision records explaining tradeoffs and failure recovery.
- Versioned CLI/MCP/JSON schemas and host setup examples.
- Provider setup pages with credentials, costs, access requirements, and troubleshooting.
- Compatibility matrix with exact app/device versions and verification mode.
- Contributor guide, development commands, issue templates, changelog, security reporting, and code ownership conventions.
- CI for core OS matrix, contracts, migrations, packaging, static checks, dependency/license scanning, and offline demo.
- A license chosen explicitly for the core, dependency/binary notices, fixture attribution, and third-party credits.
- Release artifacts with checksums, dependency locks/SBOM where practical, and reproducible build instructions.

Create/publish a GitHub repository and website only as a later explicit project action. This plan does not assume a repository name, account, hosting provider, or public release has already been authorized/configured.

### 14.3 Demo that shows engineering ability

Ship a deterministic offline demo using redistributable/generated media and recorded provider responses, clearly marked as simulated providers. It should exercise the actual domain/job/import pipeline, not only print a canned transcript.

Demo sequence: request a set → reuse an owned file → reject a wrong edit → queue one ambiguity → interrupt/restart a job → resolve the ambiguity → produce an importable crate and report. Include the resulting evidence/manifest and a clean reset command.

A separate recorded real integration demo should use permitted material and show an actual supported DJ app import. A public presentation must distinguish live integration evidence, offline fixtures, and unverified capabilities.

### 14.4 Versioning and maintenance

Version public schemas separately from application releases. Breaking changes require migration notes, compatibility windows, and fixtures. Maintain provider contract recordings and a manual/optional scheduled health check using controlled accounts/material; default CI does not call paid APIs or initiate public-network downloads.

New dependency or app versions move through contract tests and compatibility checks before being advertised as supported. Keep extraction/provider version diagnosis in doctor. Bug reports should include redacted capabilities and operation IDs, not raw private libraries or secrets.

## 15. Implementation roadmap and work packages

Each package should be small enough to review, with a user-visible outcome, affected contracts, tests/evidence, and documentation. Do not implement all providers before proving one complete path. Feature flags/capabilities protect unfinished adapters.

### M0 — Resolve critical feasibility and freeze initial contracts

Deliverables: decision log, initial supported matrix, provider/runtime choices, real integration evidence where available, schema sketches, corpus plan, and refined product defaults.

| Task | Question / experiment | Exit evidence and fallback |
| --- | --- | --- |
| M0.1 | Confirm D01–D09 and actual equipment; inventory a representative library without changing it | Recorded defaults and target app/device builds; missing equipment remains an explicit test dependency. |
| M0.2 | Prove packaged local service + CLI + stdio MCP on macOS/Windows, including client exit and restart | Persistent job survives; no port/lock/credential leaks; exact process model chosen. |
| M0.3 | Transfer controlled redistributable media through slskd, with peer queue and restart | Search/transfer/path mapping contract recorded; duplicate submission and retention-loss behavior understood. |
| M0.4 | Prove online source-link discovery, direct/SoundCloud original download, and supported YouTube set extraction with runtime dependencies | Search-provider/site list, reproducible install/doctor steps; access failures typed; unavailable providers independently disabled. |
| M0.5 | Compare recognition providers on annotated sample sets and normalize timestamps | Baseline precision/coverage/cost; provider selected; paid-feature requirements documented. |
| M0.6 | Enumerate artist catalogs with remixes/aliases and compare source coverage | Scope rules, pagination/rate policy, ambiguous cases, and known gaps recorded. |
| M0.7 | Round-trip rekordbox XML and current Serato import/crate options in disposable libraries | Initial import path verified; candidate native/app-mediated methods documented, including failures. |
| M0.8 | Inspect legacy/OneLibrary device workflows and available automation | Feasible full export strategy, supported target profile, hardware requirements, and unresolved native-writing questions. |
| M0.9 | Select runtime/dependencies/core license; review selected binary/model/data licenses | Locked baseline, supported versions, attribution plan, and SQLite/runtime checks. |

M0 gate: enough evidence exists to choose the first real collection path and avoid designing around a nonexistent API. Unavailable accounts/hardware do not block independent foundation work, but dependent native/billable capabilities cannot be marked verified. Record explicit remaining spike obligations.

### M1 — Persistent foundation and agent-ready skeleton

- [ ] M1.1 Create package, module boundaries, versioned domain contracts, config/profile system, and migrations.
- [ ] M1.2 Implement coordinator lifecycle, authenticated local API, locks, background jobs, leases, events, budgets, and capability registry.
- [ ] M1.3 Implement library/asset/source/evidence/review entities and external-root indexing.
- [ ] M1.4 Implement human/JSON CLI, no-input behavior, errors, pagination, schema introspection, and doctor.
- [ ] M1.5 Implement the initial MCP tools over the same services; verify a client can enqueue and later inspect a job.
- [ ] M1.6 Implement journaled local-file ingestion, minimum probe/decode/hash validation, quarantine, stable managed locations, request idempotency, and recovery.
- [ ] M1.7 Ship initial offline demo/fixtures and contributor setup.

Gate: local known tracklist → owned media → managed/reference collection → generic manifest works identically through CLI and MCP. Kill/restart and duplicate-request tests pass. No network provider or LLM is required for this milestone.

### M2 — Real acquisition, including Soulseek

- [ ] M2.1 Implement online source-link discovery, supported page resolution, direct downloads, and purchased-folder ingestion.
- [ ] M2.2 Implement slskd health/search/browse/transfers/path mapping/recovery and provider setup.
- [ ] M2.3 Implement yt-dlp extraction adapter and current dependency checks.
- [ ] M2.4 Add source policy, initial explainable candidate ranking, hard requested-version/duration constraints, queue patience, per-peer/source rate limits, deadlines, and alternate candidates.
- [ ] M2.5 Enforce cost/disk/concurrency reservations and typed source errors.
- [ ] M2.6 Persist missing/purchase/auth-required items and resume after repair/import.
- [ ] M2.7 Enforce transfer completion and minimum probe/decode/hash/identity checks before managed ingestion; quarantine failures. Exercise controlled real transfers/fault cases and document source access/setup.

Gate: a supplied 30-track fixture list acquires eligible missing recordings from configured sources, reuses owned media, rejects a wrong candidate, survives interrupted transfers, and returns accurate gaps. Soulseek is included in the actual demonstration, not just an interface stub.

### M3 — Trustworthy audio, metadata, and organization

- [ ] M3.1 Extend M1/M2 validation with richer identity/version matching, fingerprints, cross-encoding comparisons, and quality evidence. Retain the minimum ingestion gate on every source path.
- [ ] M3.2 Implement metadata enrichment, field provenance, user locks, format-specific tag mappings, and preservation checks.
- [ ] M3.3 Implement exact/probable duplicate handling, preferred assets, quarantines, lower-quality review, and upgrade plans.
- [ ] M3.4 Implement static/smart/source collections, ordering, taxonomy rules, and partial ready collections.
- [ ] M3.5 Implement optional measured BPM/key analysis with provenance and personal mood/energy suggestions; keep target beatgrid semantics separate.
- [ ] M3.6 Implement changesets, guarded undo, external-file reconciliation, and an audition workflow.
- [ ] M3.7 Publish matching/quality evaluation baseline and tighten automatic acceptance thresholds.

Gate: bulk imports preserve existing originals and DJ metadata, produce useful crates, and can be reviewed/recovered. User corrections persist across enrichment reruns. Lower-quality and wrong-version files cannot quietly enter a strict target collection.

### M4 — Set links to identified and sourced collections

- [ ] M4.1 Implement set URL/local-audio ingestion, description/chapter/user-tracklist evidence, and canonical set identity.
- [ ] M4.2 Implement recognition adapters, segment caching, estimated/reserved budgets, adaptive rescans, and normalized timing.
- [ ] M4.3 Implement timeline occurrences, overlaps/repeats, unknown intervals, and conflict resolution.
- [ ] M4.4 Connect accepted IDs to owned matching/acquisition and source-order collections.
- [ ] M4.5 Support correction, retry-only-gaps, provider changes, and traceable evidence reports.
- [ ] M4.6 Run held-out set evaluation and publish coverage, precision, version accuracy, timing error, cost, and limitations.

Gate: a real supported set URL produces an evidence-backed timeline and a usable partial collection with honest unknowns. Repeating the job reuses observations/assets unless a rescan is requested. A title-only or cropped-set substitute cannot satisfy exact track acquisition.

### M5 — Artist catalogs and continuing collection

- [ ] M5.1 Implement artist disambiguation, external IDs, aliases, catalog pagination, and explicit scope.
- [ ] M5.2 Implement recording/release reconciliation and credit relationships.
- [ ] M5.3 Add originals/remixes/collaborations collections and owned-gap/upgrade comparison.
- [ ] M5.4 Implement persistent catalog coverage/missing ledger and incremental sync.
- [ ] M5.5 Add opt-in recurring sync only with service lifecycle, budgets, and duplicate-prevention behavior proved.
- [ ] M5.6 Evaluate catalog inclusion and artist/version errors; document provider coverage.

Gate: an artist request yields a scoped collection, correct inclusion/exclusion decisions, reused owned files, and actionable missing entries. A later sync is incremental and does not recollect the artist's entire catalog.

### M6 — Polished LLM CLI, MCP, skills, and standalone chat

- [ ] M6.1 Complete shared public tool schemas, examples, resources, review actions, bounded summaries, and status/event behavior.
- [ ] M6.2 Package the portable skill and real Codex/Claude Code setup paths; verify clean-host installation.
- [ ] M6.3 Implement conversational CLI model adapters, session references, limits, interruption, and recovery.
- [ ] M6.4 Add natural-language musical query translation grounded in catalog/audio/user evidence.
- [ ] M6.5 Run agent scenarios, including correction, reconnect, budget exhaustion, malicious source text, and available generic/prepared partial exports. Run native-app/USB agent scenarios after M7/M8; they are required for M10, not prerequisites for beginning M6.
- [ ] M6.6 Publish redacted example transcripts and measured call/interaction outcomes.

Gate: a configured user can request a collection in each advertised interface, resolve an exception, resume later, and obtain the same underlying artifacts. Host-MCP use needs no second application model key. No interface invents success or silently expands policy.

### M7 — Verified integration with both DJ applications

- [ ] M7.1 Implement robust rekordbox XML/generic export, stable mapping, and real import reconciliation.
- [ ] M7.2 Implement the selected automatic rekordbox import method behind a tested capability gate.
- [ ] M7.3 Implement the selected Serato crate/library import method and verified fallback.
- [ ] M7.4 Implement app detection, version/schema gates, complete backup/restore, conflict checks, and preservation journals.
- [ ] M7.5 Implement analysis orchestration/readback and explicit pending states.
- [ ] M7.6 Test ordering/nesting/duplicate replay/external volumes/user edits through two app restarts.
- [ ] M7.7 Publish exact compatibility records and remaining assisted steps.

Gate: both requested apps load the resulting collections using advertised methods, with existing user data preserved. Prepared artifacts alone satisfy an interim handoff capability; they do not close the automatic-import tasks.

### M8 — Device-aware USB preparation and supported automation

- [ ] M8.1 Implement target profiles, dated compatibility data, volume identity, and preflight.
- [ ] M8.2 Implement non-destructive compatibility derivatives and storage estimates.
- [ ] M8.3 Implement the validated vendor/app-mediated export workflow and any separately proven direct writer.
- [ ] M8.4 Implement checkpoints/readback/manifests, interrupted-copy reconciliation, and partial collection revisions.
- [ ] M8.5 Validate Serato external portability separately from rekordbox player media.
- [ ] M8.6 Exercise legacy, OneLibrary, and dual-format profiles where supported, including physical player evidence.
- [ ] M8.7 Document automatic versus assisted steps and software versus hardware verification.

Gate: supported target exports load correctly, survive reconnect, retain unrelated USB contents, and report readiness accurately. Full unattended claims require actual unattended evidence. Unavailable hardware or automation remains a declared unresolved task, not an implied success.

### M9 — Simple public site and local browser interface

- [ ] M9.1 Build the early-web public project home from the verified product/docs.
- [ ] M9.2 Implement the D09-selected local interface breadth over the existing API; default to a small combined request/jobs/review control page.
- [ ] M9.3 Add accessible public content; if D09 includes local operation, add the selected local audio preview, reconnection, and bounded live controls.
- [ ] M9.4 Verify keyboard operation and mobile readability; if D09 includes local operation, verify browser authentication and origin/CSRF controls for that interface.
- [ ] M9.5 Publish only after content/demo/compatibility claims match the release and hosting is selected.

Gate: selected workflows have functional parity with headless interfaces and share policy/results. The public site accurately explains the required local component.

### M10 — Public release and full-scope acceptance

- [ ] M10.1 Exercise clean-machine installation on the advertised OS matrix.
- [ ] M10.2 Complete license/attribution, documentation, changelog, migration/recovery guides, diagnostics, and release artifacts.
- [ ] M10.3 Run deterministic CI, music evaluations, real-host agent scenarios, and advertised app/device checks.
- [ ] M10.4 Publish the offline demo and clearly distinguished real integration evidence.
- [ ] M10.5 Review every confirmed requirement R01–R11 and the D09 decision on R12 against artifacts and outstanding limitations.
- [ ] M10.6 Create/tag/publish the approved GitHub release and website through the selected accounts/workflow.

Gate: every claimed capability has evidence. All requested areas are delivered or explicitly marked unresolved with an agreed product decision. A public preview may ship earlier under narrower claims; it does not mean the full original request is complete.

### 15.1 Dependencies and sequencing

~~~mermaid
flowchart LR
    M0[M0 Feasibility] --> M1[M1 Foundation]
    M1 --> M2[M2 Acquisition]
    M2 --> M3[M3 Music and organization]
    M3 --> M4[M4 Sets]
    M3 --> M5[M5 Artists]
    M1 --> M6[M6 Agent experience]
    M4 --> M6
    M5 --> M6
    M3 --> M7[M7 DJ apps]
    M7 --> M8[M8 USB]
    M6 --> M9[M9 Web]
    M8 --> M9
    M9 --> M10[M10 Full release]
~~~

Begin M7/M8 compatibility research in M0; do not wait until all discovery features are built to discover an impossible target integration. Basic JSON/MCP ship in M1, while M6 completes the experience across full workflows. Independent provider/evaluation tasks may proceed in parallel behind stable contracts.

### 15.2 Requirement traceability

| Requirement | Main milestones | Final evidence |
| --- | --- | --- |
| R01–R02 Set URLs and IDs | M0, M2, M4 | Real source ingestion, annotated timeline, recognition report, sourced partial collection. |
| R03 Bulk acquisition | M1–M3 | Large-job benchmark, recovery tests, controlled transfers, missing ledger. |
| R04 Artist catalogs | M3, M5, M6 | Scoped artist workflow, incremental sync, inclusion/version evaluation. |
| R05 Soulseek | M0, M2 | Real controlled-peer search/transfer/recovery and setup guide. |
| R06 Serato and rekordbox | M0, M7 | Both app round trips, preservation evidence, compatibility records. |
| R07 USB | M0, M8 | Device preflight/export/readback and stated hardware evidence. |
| R08 LLM CLI/MCP/skill | M1, M6 | Real host setup and agent scenario results. |
| R09 Standalone chat | M6 | Model-configured end-to-end request, correction, and resume. |
| R10 GitHub | M1, M10 | Installable release, docs, licensed demo, CI, evaluation artifacts. |
| R11 Public home | M9, M10 | Published accessible static site with accurate claims. |
| R12 Proposed local web UX | M9, subject to D09 | Selected interface breadth works with headless-policy parity. |

## 16. Risk register and decision checkpoints

| Risk | Early evidence / mitigation | Decision checkpoint |
| --- | --- | --- |
| Recognition coverage is weak on the user's music | Evaluate relevant mixes; preserve unknowns; combine text and audio evidence; support alternate providers. | M0.5, M4.6 |
| Wrong edits make bulk acquisition untrustworthy | Version-aware constraints, calibrated acceptance, post-download checks, user review, negative fixtures. | M3.7 |
| Provider access or websites change | Isolated adapters, health/version diagnostics, recorded contracts, typed gaps, alternatives. | Each provider release |
| Soulseek queues/retention/path mappings break jobs | Persist own receipts, bounded queue policy, reconcile external IDs/files, controlled fault tests. | M0.3, M2.7 |
| App internals are unsupported or change | Exact version/schema gates, disposable libraries, backups, replaceable adapters, assisted mode. | M0.7, M7 |
| USB libraries or firmware assumptions are wrong | Manufacturer-linked profiles, software readback, actual hardware, distinct formats. | M0.8, M8 |
| Existing files or cues are damaged | Read/index default, staged owned writes, complete backups, preservation fixtures, guarded undo. | M1.6, M3, M7 |
| Agent retries duplicate effects | Request hash/idempotency keys, journals, provider reconciliation, reconnect scenarios. | M1, M6 |
| Model/audio API costs expand unexpectedly | Atomic budget reservations, explicit caps, caching, per-provider usage report. | M2.5, M4.2, M6.3 |
| Setup is too complex for normal DJs | Useful offline demo, optional dependencies, doctor, one primary Soulseek path, later installers. | M0.2, M10.1 |
| Full scope overwhelms implementation | Vertical milestones, narrow provider interfaces, tested feature flags, honest preview releases. | Every milestone review |
| Demo looks impressive but is unrepresentative | Difficult fixtures, held-out evaluation, real app evidence, clear simulated/live distinction. | M10.3–M10.4 |

Decision records should include context, alternatives considered, chosen approach, evidence links, consequences, and revisit triggers. Initial records: local architecture, library ownership, action policy, identity model, recognition provider, Soulseek backend, target-app methods, device export, public schema versioning, and license/distribution.

## 17. Definition of complete and first implementation handoff

The full product is complete when a newly configured user can use an advertised LLM CLI or direct command to collect from a supported set or artist, reuse existing media, acquire eligible files including through Soulseek, inspect/correct uncertainties, retain a clean organized library, use the collection in both requested DJ applications through verified methods, and prepare supported USB media with truthful readiness. The standalone chat, public home, installable GitHub release, documentation/evaluation evidence, and local web scope selected in D09 also belong to the completed scope.

Under the proposed D04 baseline, the requested automatic/minimal-intervention app import and device export must be delivered for the supported target matrix. Assisted-only paths leave those automation tasks unresolved unless the user explicitly chooses a narrower product contract and that decision is recorded.

External catalog coverage, source availability, paid-service access, and exact app/device compatibility remain documented constraints. “Complete” does not imply every recording exists online or every player/version is supported.

The first implementation handoff is M0 followed by M1. Incorporate available answers to D01–D09 and choose the first real app/device test profile before dependent work. The provisional directory/name and independent foundation do not require all answers to proceed. Every implementation change should identify its work-package IDs and include the validation appropriate to its risk.

The first user-visible checkpoint is a durable, agent-operable local collection workflow with a real schema, review queue, owned-file reuse, and an honest export manifest. The next checkpoint adds real Soulseek/direct acquisition. These establish the reliability that the full discovery, catalog, app, USB, and web experiences depend on.

## 18. Research provenance

External capabilities were checked against primary project/vendor documentation on 2026-10-02. Linked documentation establishes feasibility and constraints; it does not establish tested compatibility of this unimplemented application. Recheck live dependencies during M0 and before release.

Primary references are linked alongside the decisions they support above. Keep implementation evidence under docs/compatibility and evaluation reports under evals/reports, recording exact runtime, provider, app, OS, player/firmware, input fixture, observed behavior, and date. Preserve failed experiments as evidence when they explain an integration choice.
