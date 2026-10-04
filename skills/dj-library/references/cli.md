# CLI recipes

All application commands emit an envelope with `schema_version`, `ok`, `result`, `warnings`, and `error`. No `--json` option is needed. Supply an initialized workspace explicitly. Do not pass private user paths into public repository examples or commits.

```bash
djlib --workspace /path/to/workspace capabilities
djlib --workspace /path/to/workspace library --query "Joy Orbison"
djlib --workspace /path/to/workspace source-inspect 'https://soundcloud.com/USER/SET'
```

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

Start with `djlib delivery targets`, then a `delivery plan --file pilot.json` request:

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

These are example target choices, not universal defaults. `audio_mode` defaults to `preserve`; explicit `mp3_320` and `wav16_44100` create compatibility copies without improving source fidelity. `serato_portable` is a separate workflow for a Serato computer. Player profiles cover only the documented models returned by the tool.

```bash
djlib --workspace PATH delivery prepare DELIVERY_ID --revision 1 --key pilot-preparation-v1
djlib --workspace PATH jobs get JOB_ID
djlib --workspace PATH delivery get DELIVERY_ID
djlib --workspace PATH delivery bind-device DELIVERY_ID /Volumes/ACTUAL_USB --revision CURRENT
```

Follow the job's named playlists, immutable `delivery-manifest.json` and `NATIVE_STEPS.txt` through the native app. No djlib command performs native import, analysis, USB export or eject. Record an actual stage with `delivery observe DELIVERY_ID --file observation.json`. Get the current revision before each observation. Required fields are `revision`, `stage`, actual `app_version`, `track_count`, `playlist_counts` keyed by collection ID, `checked_recording_ids`, `observer`, `notes`, `method` and `outcome`. Read `djlib schemas` for exact constraints.

Stages are `imported`, `analyzed`, `native_exported`, `device_library_checked`, and `hardware_playback`. Use `native_app_ui` for the first four and `physical_hardware` for actual playback. Successful native observations cover all frozen IDs/counts; hardware playback covers all pilot tracks and may sample a full delivery. Failed observations preserve real partial counts and invalidate later evidence.

A successful rekordbox `hardware_playback` observation also needs the matching `hardware_profile`, actual nonblank `firmware_version`, and `storage_recognized: true`. CDJ-3000 firmware 3.30 was withdrawn and cannot pass. Analysis reconciliation is synchronous; changed working-copy tags require audio checks and may take time. If a request times out, read the delivery again before retrying its revision.

After native export and device-library inspection:

```bash
djlib --workspace PATH delivery verify-device DELIVERY_ID --revision CURRENT
```

Test hardware playback, record it, reconnect and run a fresh verification before ejecting. `delivery get` never freshly verifies audio hashes. `ready_for_departure` can only be true in a fresh successful verification response with all required operator/native evidence present. A full request uses `phase: "full"` and a matching `pilot_delivery_id`; it cannot prepare before that pilot passes. The snapshot does not grow as later downloads finish.
