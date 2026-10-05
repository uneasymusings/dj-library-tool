# CLI recipes

All application commands emit an envelope with `schema_version`, `ok`, `result`, `warnings`, and `error`. No `--json` option is needed. Supply an initialized workspace explicitly. Do not pass private user paths into public repository examples or commits.

`requests`, `organize`, and `delivery` below are a3 interfaces; they are not available in the older a2 wheel. Check `version`, `capabilities` and connected tool schemas before using them.

```bash
djlib --workspace /path/to/workspace capabilities
djlib --workspace /path/to/workspace library --query "Joy Orbison"
djlib --workspace /path/to/workspace source-inspect 'https://soundcloud.com/USER/SET'
```

## Exact requests and unknown IDs

The library workflow exposes these MCP tools, with corresponding CLI groups:

| MCP tool | CLI | Purpose |
| --- | --- | --- |
| `djlib_create_request` | `requests create --file FILE` | Persist a named list and hash-check catalog matches. |
| `djlib_request` | `requests get REQUEST_ID` | Read saved, paginated coverage with timestamps. |
| `djlib_refresh_request` | `requests refresh REQUEST_ID --revision N` | Refresh all or selected item IDs against current files. |
| `djlib_resolve_request` | `requests resolve REQUEST_ID ITEM_ID --file FILE` | Select a source, explicitly satisfy an identity, or clear a selection. |
| `djlib_request_report` | `requests report REQUEST_ID --revision N` | Save a revision-specific JSON missing report. |
| `djlib_track_metadata` | `organize metadata RECORDING_ID --asset-revision-id ID` | Read hash-matching embedded metadata and provenance. |
| `djlib_annotations` | `organize get RECORDING_ID --asset-revision-id ID` | Read saved byte-bound annotations/revision. |
| `djlib_annotate` | `organize annotate --file FILE` | Patch supplied catalog annotations without changing original tags. |
| `djlib_organize` | `organize collection --file FILE` | Create an ordered collection from exact catalog references. |

Save `wanted.json`:

```json
{
  "name": "Friday requests",
  "idempotency_key": "friday-requests-v1",
  "items": [
    {"artist": "Artist", "title": "Track", "version": "Extended Mix"},
    {"kind": "unknown", "label": "Unidentified set track", "timestamp": "00:47:12"}
  ]
}
```

```bash
djlib --workspace PATH requests create --file wanted.json
djlib --workspace PATH requests get REQUEST_ID --after 0 --limit 100
djlib --workspace PATH requests refresh REQUEST_ID --revision CURRENT --item-id ITEM_ID
djlib --workspace PATH requests report REQUEST_ID --revision CURRENT
```

Named requests require artist/title and preserve the explicit version; an empty version is not a wildcard for every mix. Unknown entries need a label plus timestamp or HTTPS source URL, with no guessed artist/title. Lists accept up to 1,000 entries. Reuse the creation key after a lost response; a different list needs a different key.

The response has `request_id`, `revision`, saved counts/items and `next_offset`. Pass `next_offset` as CLI/MCP `after` to page requests. Reads do not recheck files. Create/refresh/resolution checks have a shared 1 GiB/30-second budget and at most 20 candidate revisions per item; an incomplete check remains unavailable. Refresh selected `--item-id` values in separate bounded batches using the new revision each time.

Outcomes distinguish `satisfied`, `missing`, `ambiguous`, `unknown`, `unavailable` and `source_selected`. Only normalized artist/title/version equality permits automatic reuse; ambiguous byte revisions need explicit selection. A resolution file has current `revision` and `action`: `select_source` requires `source_url`; `satisfy` requires `recording_id` plus evidence `notes` and can select `asset_revision_id`; `clear` removes the explicit selection. Selecting a source does not search, download, or mark the request satisfied. After separately acquired tracks enter the catalog, refresh and inspect the ledger. A missing local match does not prove worldwide unavailability.

## Catalog notes and ordered collections

Read `organize metadata` and `organize get` for exact recording/revision IDs before editing. Missing/malformed/conflicting BPM/key tags remain unknown; known embedded values have `verified: false`. Annotation revision `0` creates the first row. Example `annotations.json`:

```json
{
  "recording_id": "RECORDING_ID",
  "asset_revision_id": "ASSET_REVISION_ID",
  "revision": 0,
  "idempotency_key": "friday-notes-v1",
  "tags": ["warm", "vocals"],
  "set_role": "Warm Groove",
  "notes": "Review the vocal entrance before ordering the opening tracks."
}
```

```bash
djlib --workspace PATH organize annotate --file annotations.json
```

Only supplied fields change; null clears a field. Optional fields include `genres`, `energy` (1–10), `bpm` and `key`. BPM/key objects require `value` and `source` (`operator` or `native_tag`), with `verified` defaulting to false. `native_tag` must match fresh embedded catalog evidence. A verified flag records operator judgment, not an engine measurement. Do not invent values or translate keys automatically. Annotation updates preserve original audio/tags.

Example `organized.json`:

```json
{
  "name": "Warm Groove",
  "tracks": [{"recording_id": "RECORDING_ID", "asset_revision_id": "ASSET_REVISION_ID"}],
  "filters": {"tags": ["warm"]},
  "unknown": "exclude",
  "order_by": "bpm",
  "idempotency_key": "friday-warm-groove-v1"
}
```

```bash
djlib --workspace PATH organize collection --file organized.json
```

The completed job returns a frozen collection and item outcomes. Review exclusions; recordings can appear in several collections. Up to 1,000 explicit references are accepted. Filter fields are `bpm_min`, `bpm_max`, `keys`, `genres`, `tags`, `set_roles` and `require_verified`. Active categories combine with AND; labels within a category are alternatives. Unknown/unverified filter values use `exclude` by default, or explicit `include`/`error`. Sorting supports `input`, `artist`, `title`, `bpm`, `key` or `energy`, with `descending`; unknown sort values remain last. Key filters match supplied labels; wheel labels sort numerically, without translating between key systems or checking harmonic compatibility. Stable keys return the original result; changed organization needs a new key. Metadata/organization hashing is synchronous (180-second client timeout), not a background analysis job; inspect state after a lost response and keep large selections bounded.

## Owned tracks

Write a request file:

```json
{
  "name": "Friday warm-up",
  "profile": "club",
  "tracks": [
    {"path": "/allowed/music/track.flac", "artist": "Artist", "title": "Track", "version": "Extended Mix"}
  ]
}
```

`club` indexes in place; `archive` copies accepted audio into managed storage.

```bash
djlib --workspace /path/to/workspace plan --file /path/to/request.json
djlib --workspace /path/to/workspace start PLAN_ID --revision 1 --key friday-v1
```

## Selected recording downloads

```json
{
  "name": "Friday web selections",
  "idempotency_key": "friday-web-v1",
  "tracks": [
    {"url": "https://soundcloud.com/ARTIST/RECORDING", "artist": "Artist", "title": "Track", "version": ""}
  ]
}
```

```bash
djlib --workspace /path/to/workspace download --file /path/to/downloads.json
```

URLs are placeholders. These commands do not search the web or identify set audio. The optional download dependency and FFmpeg must be installed. Acquisition is limited to 1,000 selections per request and 30-minute non-live recordings. Failed selections remain inspectable in the durable job.

## Follow progress and export

```bash
djlib --workspace /path/to/workspace jobs get JOB_ID
djlib --workspace /path/to/workspace jobs items JOB_ID --state failed
djlib --workspace /path/to/workspace jobs wait JOB_ID --timeout 30
djlib --workspace /path/to/workspace reviews list --job-id JOB_ID
djlib --workspace /path/to/workspace reviews resolve REVIEW_ID --revision 1 --choice skip
djlib --workspace /path/to/workspace collection COLLECTION_ID
djlib --workspace /path/to/workspace export COLLECTION_ID --key friday-export-v1
djlib --workspace /path/to/workspace usb-preflight /Volumes/DJ_USB --required-bytes 1000000000
```

Use `next_cursor` for paging: collection/items use `--after`. Review choices are `accept_requested`, `use_file_metadata`, or `skip`. Exit 2 means an input/application error; `jobs wait` exits 3 when still running and 4 for attention/failure/partial outcomes. The daemon keeps accepted work after the calling CLI or MCP process exits.

Inspect current schemas with `djlib schemas`. Stop the coordinator using `djlib --workspace PATH service stop`; accepted unfinished intents resume when the service starts again. Cancellation preserves already accepted files. This version does not remove staging or incoming media automatically.

## Native DJ delivery

For local app preparation, no exact hardware model or USB is needed. Read `djlib --workspace PATH delivery targets`, then create `app-pilot.json`:

```json
{
  "name": "Local app pilot",
  "collection_ids": ["COLLECTION_ID"],
  "workflow": "rekordbox_import",
  "app_version": "ACTUAL_INSTALLED_VERSION",
  "audio_mode": "preserve",
  "phase": "pilot",
  "pilot_size": 3
}
```

Choose `serato_import` for Serato. Omit `hardware_profile` for both app-only workflows. App profiles assess a conservative documented input subset, not musical accuracy or standalone player compatibility.

Plans freeze explicit catalog annotations and their revision in `dj_metadata`. Preparation writes supplied BPM/key/genre tags and notes/tags/role/energy comments into separate WAV/AIFF/MP3, FLAC or MP4 working copies; originals remain untouched. Where no override is supplied, existing supported embedded tags remain. The manifest preserves exact annotations/provenance. MP4's integer `tmpo` cannot hold fractional BPM: the exact value is retained in a freeform tag/manifest with a warning, never rounded into a false tempo. Check actual native display and analysis after import; tag writing is not acoustic analysis or an accuracy guarantee. Later annotation changes do not alter an already frozen delivery.

```bash
djlib --workspace PATH delivery plan --file app-pilot.json
djlib --workspace PATH delivery prepare DELIVERY_ID --revision CURRENT --key app-pilot-v1
djlib --workspace PATH jobs get JOB_ID
djlib --workspace PATH delivery get DELIVERY_ID
```

After all preparation items succeed, import the named playlists/files and analyze the new working copies in the native app. Record actual `imported` and `analyzed` observations as below, then:

```bash
djlib --workspace PATH delivery verify-app DELIVERY_ID --revision CURRENT
```

MCP equivalent: `djlib_verify_delivery_app(delivery_id, revision)`. Fresh successful `ready_for_app_use` verifies working files and depends on the recorded native observations. `delivery get` does not perform a fresh check, and neither action grants USB readiness. This completes the app-only scope without device binding/playback evidence. A later USB delivery needs its own matching pilot.

For rekordbox, export a supported snapshot through **File > Export Collection in xml format** into the allowed workspace, then inspect its exact working paths, playlist membership/order and declared app version:

```bash
djlib --workspace PATH delivery inspect-native-xml DELIVERY_ID /absolute/allowed/native.xml --revision CURRENT
```

MCP equivalent: `djlib_inspect_delivery_native_xml(delivery_id, revision, path)`. This is a read-only artifact comparison. It opens no media paths from the XML, changes no delivery stage and never grants app/device readiness. Engine-generated experimental XML is rejected as native evidence. Treat missing/ambiguous membership and version differences as unresolved; check BPM/key/grid accuracy and track loading in the app. An exported snapshot can become stale and is not proof of current native state or USB playback.

The report retains per-recording raw BPM/key and unknowns. If using a known value for sorting, create an explicit `source: "operator"` annotation against the corresponding catalog recording/revision, with the XML checksum and declared version in notes. Keep it unverified until musical review. XML evidence is not `native_tag` evidence from the catalog file, and missing/ambiguous values must stay unset. Rebuild filtered collections and delivery snapshots after the chosen annotation update. The snapshot schema follows [AlphaTheta's XML specification](https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf).

For standalone rekordbox USB delivery, create a separate `pilot.json` with actual target details:

```json
{
  "name": "Warm-up pilot",
  "collection_ids": ["collection_FROM_CATALOG"],
  "workflow": "rekordbox_usb",
  "app_version": "ACTUAL_INSTALLED_VERSION",
  "hardware_profile": "cdj-2000nxs",
  "audio_mode": "wav16_44100",
  "phase": "pilot",
  "pilot_size": 3
}
```

These are example target choices, not universal defaults. `audio_mode` defaults to `preserve`; explicit `mp3_320` and `wav16_44100` create compatibility copies without improving source fidelity. `serato_portable` is a separate workflow for a Serato computer and does not take a standalone hardware profile. Player profiles cover only the documented models returned by the tool.

```bash
djlib --workspace PATH delivery plan --file pilot.json
djlib --workspace PATH delivery prepare DELIVERY_ID --revision CURRENT --key pilot-preparation-v1
djlib --workspace PATH jobs get JOB_ID
djlib --workspace PATH delivery get DELIVERY_ID
djlib --workspace PATH delivery bind-device DELIVERY_ID /Volumes/ACTUAL_USB --revision CURRENT
```

Follow the job's named playlists, frozen `delivery-manifest.json` and `NATIVE_STEPS.txt` through the native app. No djlib command performs native import, analysis, USB export or eject. Record an actual stage with `delivery observe DELIVERY_ID --file observation.json` (MCP `djlib_observe_delivery`). Get the current revision before each observation. Required fields are `revision`, `stage`, actual `app_version`, `track_count`, `playlist_counts` keyed by collection ID, `checked_recording_ids`, `observer`, `notes`, `method` and `outcome`. Read `djlib schemas` for exact constraints.

Stages are `imported`, `analyzed`, `native_exported`, `device_library_checked`, and `hardware_playback`. Use `native_app_ui` for the first four and `physical_hardware` for actual playback. Successful native observations cover all frozen IDs/counts; hardware playback covers all pilot tracks and may sample a full delivery. Failed observations preserve real partial counts and invalidate later evidence.

A successful rekordbox USB `hardware_playback` observation also needs the matching `hardware_profile`, actual nonblank `firmware_version`, and `storage_recognized: true`. CDJ-3000 firmware 3.30 was withdrawn and cannot pass, including v/case variants. Analysis reconciliation is synchronous; changed working-copy tags require audio checks. Observation/app-verification clients allow 600 seconds; larger changed batches can exceed that. If a request times out, read the delivery again before retrying its revision.

After native export and device-library inspection:

```bash
djlib --workspace PATH delivery verify-device DELIVERY_ID --revision CURRENT
```

Test hardware playback, record it, reconnect and run a fresh verification before ejecting. `delivery get` never freshly verifies audio hashes. `ready_for_departure` can only be true in a fresh successful verification response with all required operator/native evidence present. A full request uses `phase: "full"` and a matching `pilot_delivery_id`; it cannot prepare before that pilot passes. The snapshot does not grow as later downloads finish.
