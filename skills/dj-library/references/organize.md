# Organize: notes, BPM/key, filtered crates, changed files

The default MCP profile has `djlib_import_rekordbox_analysis`, `djlib_roots` and `djlib_add_roots`; the other tools below need `DJLIB_MCP_TOOLS=full` in the server's environment, and the CLI always has them. They work on exact catalog identities: a `recording_id` plus the `asset_revision_id` of the exact bytes.

| MCP tool | CLI | Purpose |
| --- | --- | --- |
| `djlib_track_metadata` | `organize metadata RECORDING_ID --asset-revision-id ID` | Embedded BPM/key/genre/comments of these exact bytes. |
| `djlib_annotations` | `organize get RECORDING_ID --asset-revision-id ID` | Saved notes and their revision. |
| `djlib_annotate` | `organize annotate --file FILE` | Patch notes, tags, role, energy, BPM/key. |
| `djlib_organize` | `organize collection --file FILE` | An ordered, filtered collection from exact references. |
| `djlib_import_rekordbox_analysis` | `rekordbox sync` / `rekordbox pull` | rekordbox's BPM, cues and key into annotations. |
| `djlib_reconcile` | `reconcile --file FILE` | Accept a file that changed on disk, explicitly. |
| `djlib_roots`, `djlib_add_roots` | `roots list`, `roots add PATH` | Music folders djlib may read. |

## Evidence rules

- Tags are unverified evidence. Missing, malformed or conflicting BPM/key stay unknown.
- rekordbox's analysis arrives with `source: rekordbox_analysis` and `verified: false`; values the user set are kept. Call them rekordbox's analysis, not facts.
- Mark a value `verified` only after an actual review by the user. Don't invent BPM, key, energy, mood or genre.
- Annotations never retag original files or set cues.

## Annotate

Read `organize metadata` and `organize get` first. Revision `0` creates the first annotation.

```json
{
  "recording_id": "RECORDING_ID",
  "asset_revision_id": "ASSET_REVISION_ID",
  "revision": 0,
  "idempotency_key": "friday-notes-v1",
  "tags": ["warm", "vocals"],
  "set_role": "Warm Groove",
  "energy": 4,
  "notes": "Vocal entrance at 1:10."
}
```

Only supplied fields change, and `null` clears a field. Other fields: `genres`, `bpm`, `key`. BPM and key are objects with `value` and `source` (`operator` or `native_tag`; `native_tag` must match the file's current tags) and `verified` (default false). Keep subjective tags, role and energy separate from measured BPM/key.

## Filtered, ordered collections

```json
{
  "name": "Warm Groove",
  "tracks": [{"recording_id": "RECORDING_ID", "asset_revision_id": "ASSET_REVISION_ID"}],
  "filters": {"tags": ["warm"], "bpm_min": 118, "bpm_max": 124},
  "unknown": "exclude",
  "order_by": "bpm",
  "idempotency_key": "friday-warm-groove-v1"
}
```

- Filters: `bpm_min`, `bpm_max`, `keys`, `genres`, `tags`, `set_roles`, `require_verified`. Categories combine with AND; values within one are alternatives.
- `unknown` is `exclude` (default), `include` or `error`. Sorting: `input`, `artist`, `title`, `bpm`, `key` or `energy`, optionally `descending`; unknown values sort last.
- Key filters match labels as written; wheel labels (8A) sort numerically, and keys are never translated between systems.
- The reply is a job: wait for it, read the per-item exclusions, then use the `collection_id`. Up to 1,000 references; collections may overlap.

## Saved work and folders

Catalog, jobs and saved lists take `query`, `limit` (1–100) and an opaque `after`; pass `next_cursor` back with the same query until it is null. Rows show recorded locations, not freshly checked availability. A collection's app/device state is `not_tracked_here`, which doesn't mean rekordbox lacks it.

Add only folders the user names: `djlib_add_roots` with `{"paths": ["/Users/NAME/Music/New"]}`. Existing roots stay and no music moves. `init --allow-root` on an existing workspace returns `ROOTS_NOT_UPDATED` instead of adding roots.

## A file changed on disk

Don't silently repoint a crate at changed bytes. Submit an explicit reconciliation with the old revision and the SHA-256 of the current file:

```json
{
  "idempotency_key": "changed-file-v1",
  "items": [{
    "path": "/Users/NAME/Music/track.flac",
    "expected_asset_revision_id": "OLD_ASSET_REVISION_ID",
    "expected_sha256": "SHA256_OF_THE_CURRENT_FILE",
    "action": "tag_only"
  }]
}
```

- `tag_only` requires unchanged decoded audio and stream format; it keeps the recording and adds a revision. `AUDIO_BASELINE_UNAVAILABLE` means it can't be proven; don't assume.
- `replace_audio` treats the new bytes as a known revision or a provisional identity, never as the old song by assumption.
- Neither edits audio. Old memberships and annotations stay pinned to the old revision; affected request matches and deliveries are invalidated, so rebuild the selections the user needs.
