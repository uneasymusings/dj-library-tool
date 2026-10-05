# Changelog

## 0.1.0a4

Local validation passed 498 tests with four Windows-only skips; Ruff checks passed for 95 files. The installed-wheel smoke check passed actual 40-tool MCP calls and three original tones. Release CI and public-artifact verification remain pending; historical a3 evidence is a separate baseline.

- Added paginated catalog and saved collection/request/delivery discovery, explicit additive music-root configuration, and field-level CLI input errors. Collection native state is `not_tracked_here`, not an inferred import/export result.
- Corrected new recording identity handling for symbol-only labels and untagged files. Existing incorrect merges are not automatically repaired.
- Added explicit durable reconciliation for changed catalog locations: `tag_only` checks decoded audio and stream properties; `replace_audio` preserves history and uses the new bytes' identity. Old collection revisions and annotations are not silently upgraded or inherited.
- Moved organization, passed analysis/native-export observations and app verification into durable jobs with item progress and generation-fenced evidence commits. Native-check retries derive their key from the complete intent; completed receipts point to saved delivery evidence, not a fresh readiness result.
- Allowed full local preparation without a physical pilot. Invalid supplied pilot claims still fail; native/device/playback readiness gates remain. Pilot and full working copies remain separate.
- Added bounded, read-only Windows partition-style queries; query failures preserve known volume identity and leave partition style unknown.
- Added checked catalog backups before automatic schema upgrades. This backs up the engine catalog only, not music or native DJ databases, and does not provide a one-command restore workflow.

No new native Serato, real-music, USB export or hardware playback validation is claimed. See [status](docs/STATUS.md) for pending release checks.

## 0.1.0a3

This alpha separates local library preparation, native app import, native USB export and hardware playback. A file transfer or M3U/XML artifact cannot mark a DJ USB ready.

- Added app-only rekordbox and Serato delivery workflows that need no player/controller model. Player-specific USB preparation remains a separate workflow.
- Added frozen collection/annotation snapshots, isolated working copies, format checks, explicit native observations, fresh app-file verification and read-only device identity/hash checks.
- Added read-only native rekordbox XML comparison for exact paths, playlist membership/order and exported app version. This compares a snapshot; it never advances readiness or opens media paths from XML.
- Added durable exact-recording requests, owned-file matching, explicit unknown/ambiguous versions, selected-source outcomes and missing-track reports.
- Added revisioned annotations, BPM/key provenance, notes/categories/roles/energy and ordered collections from existing catalog IDs. Explicit metadata travels to working-copy tags/comments; originals remain untouched.
- Added fair job scheduling, supported JavaScript runtime selection and 34 MCP workflow tools with corresponding JSON CLI commands and strict input contracts.
- Added explicit coordinator version checks before requests, preventing a new CLI from silently using an older running engine while preserving status/capabilities/shutdown access.
- Added database migrations preserving the a2 catalog and packaged assistant setup/skill resources. CI requires FFmpeg/ffprobe on Linux, macOS and Windows across Python 3.12/3.13; release drafting waits for this matrix.
- Corrected Windows temporary-copy flushing and file identity checks. USB readback uses held directory/file handles and rejects reparse points; filesystem and hardware validation remain separate from CI.

Before upgrading, stop the coordinator for the library workspace and preserve a backup of its engine catalog/configuration. Installation does not replace music or native DJ databases. Start the updated engine and run `doctor`; packaged migrations apply when its catalog opens. Regenerate a separate assistant session to receive the new tools and skill. Existing sessions/configuration remain unchanged.

Native import, musical BPM/key/grid review, cues, native export and eject remain actions in rekordbox/Serato. Serato native import, physical USB export and player playback need separate supervised validation. This release does not add automatic acoustic set recognition, exhaustive artist catalogs, Soulseek, automatic genre/energy inference or a standalone assistant model. See [coverage](docs/COVERAGE.md) and [validation](docs/VALIDATION.md).

## 0.1.0a2

Published the local engine, JSON CLI, 16 MCP tools, migrations, complete assistant skill, public selected-recording downloads, explicit collections, generic handoff artifacts and read-only USB preflight. The release included original-tone demos and separate Codex/Claude session setup.
