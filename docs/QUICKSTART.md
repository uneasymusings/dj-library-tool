# Quickstart

These recipes describe **0.1.0a7**; install using [INSTALL](INSTALL.md) and consult [status](STATUS.md) for validation/publication evidence. Earlier a3 lacks discovery/reconciliation commands and runs organization/native checks synchronously. Check `djlib version` and `capabilities`. Use installed `djlib` below; contributors can prefix it with `uv run` in their checkout.

## 1. Choose a workspace

```bash
djlib --workspace /path/to/dj-workspace init --allow-root /path/to/music
djlib --workspace /path/to/dj-workspace doctor
```

The workspace stores its catalog, runtime token, media, and exports. Its directory must be empty on first initialization. Repeat initialization returns existing configuration only when no new roots are requested. Use `djlib --workspace PATH roots list` and `roots add /actual/music/folder` to explicitly add an existing folder. New roots passed to an existing workspace's `init` return `ROOTS_NOT_UPDATED`. Symlinks outside allowed roots are rejected; adding access never moves music.

Always specify `--workspace` before the subcommand, or set `DJLIB_WORKSPACE`. The default is `~/.local/share/djlib/default`; initialization is explicit. `doctor` reports FFmpeg, ffprobe, optional yt-dlp, Deno, and Node without starting the coordinator. Version a3 additionally checks JavaScript runtime versions and reports which supported runtime was selected; the public a2 availability check does not establish runtime compatibility. Neither check guarantees provider extraction success.

To try this through a fresh AI CLI, run `setup-agent --output /absolute/path/new-session`, then use that folder's `launch.py`. See [agent setup](AGENTS.md).

On Windows, launch that script from an external terminal so it starts the coordinator before the assistant host. For manual MCP configuration, run `djlib --workspace PATH service start` externally first. `COORDINATOR_START_REQUIRED` means the Windows MCP process will not cold-start a coordinator within the host's lifetime-controlled process job.

## 2. Index owned audio

```bash
djlib --workspace /path/to/dj-workspace scan /path/to/music --key music-scan-v1
```

Save `result.job_id` from the response:

```bash
djlib --workspace /path/to/dj-workspace jobs wait JOB_ID --timeout 30
djlib --workspace /path/to/dj-workspace jobs items JOB_ID
djlib --workspace /path/to/dj-workspace library --query "Artist"
```

Scans index WAV, MP3, FLAC, AIFF, and M4A files in place, using embedded artist/title tags or filename fallbacks. A hash proves byte identity, not musical identity. Untagged entries retain provisional byte-based identity; identical filenames do not establish the same recording. New symbol-only labels stay distinct. Existing mistaken merges from older versions are not automatically repaired. Large scans are limited to 10,000 supported files; choose subfolders for larger libraries. Original files are never renamed or retagged.

### Find saved work and page the catalog

```bash
djlib --workspace PATH library --query "Artist" --limit 100
djlib --workspace PATH library --query "Artist" --limit 100 --after NEXT_CURSOR
djlib --workspace PATH collections --query "Warm"
djlib --workspace PATH requests list --query "Friday"
djlib --workspace PATH delivery list --query "Friday"
djlib --workspace PATH jobs list --query "Friday"
```

Continue with each response's opaque `next_cursor` and the same query until null. Catalog rows group a recording/revision with its locations; a listed location has not been freshly verified. Paging fixes an insertion cutoff, not a transaction-wide snapshot of mutable metadata. Collection app/device state is `not_tracked_here`: use a delivery's evidence and a new verification for readiness.

### Reconcile a file edited outside the tool

Do not rescan changed bytes and assume old playlists now reference them. Read the current catalog revision and compute the changed file's SHA-256, then submit an explicit request:

```json
{
  "idempotency_key": "retagged-track-v1",
  "items": [{
    "path": "/allowed/music/track.flac",
    "expected_asset_revision_id": "OLD_ASSET_REVISION_ID",
    "expected_sha256": "REPLACE_WITH_CURRENT_NEW_FILE_64_CHARACTER_SHA256",
    "action": "tag_only"
  }]
}
```

```bash
djlib --workspace PATH reconcile --file reconciliation.json
djlib --workspace PATH jobs wait JOB_ID --timeout 30
djlib --workspace PATH jobs items JOB_ID
```

The SHA-256 above is a placeholder for the **current changed file**, not the old catalog hash. `tag_only` requires matching decoded audio and stream properties, preserving recording/asset identity while adding a byte revision. A legacy revision without a decoded baseline needs another verified location of its original bytes; otherwise it returns `AUDIO_BASELINE_UNAVAILABLE`. Choose `replace_audio` only for an intended payload change: it reuses exact known bytes or creates a provisional identity, never assumes this is still the old song. Neither action edits the audio. Old memberships, annotations and snapshots retain their selected revisions; review and rebuild them explicitly. Affected deliveries/request matches are invalidated, and historical exports are marked stale rather than rewritten.

### Track exact requests

Before acquiring more music, save the intended songs and unresolved set IDs in `wanted.json`:

```json
{
  "name": "Friday requests",
  "idempotency_key": "friday-requests-v1",
  "items": [
    {"artist": "Artist", "title": "Track", "version": "Extended Mix"},
    {"kind": "unknown", "label": "Unidentified track in the set", "timestamp": "00:47:12"}
  ]
}
```

```bash
djlib --workspace /path/to/dj-workspace requests create --file wanted.json
djlib --workspace /path/to/dj-workspace requests get REQUEST_ID --after 0 --limit 100
djlib --workspace /path/to/dj-workspace requests refresh REQUEST_ID --revision CURRENT_REVISION
djlib --workspace /path/to/dj-workspace requests report REQUEST_ID --revision CURRENT_REVISION
```

Use returned IDs and read the current revision after mutations. Matching uses normalized artist/title/version labels; another mix is not an automatic substitute. Multiple matching byte revisions remain ambiguous. Reads show saved evidence and timestamps; create/refresh/resolution performs bounded file checks. For a large list, repeat `refresh` with selected `--item-id ITEM_ID` options and the latest revision. Unknown IDs remain explicit. Selecting a source through `requests resolve` records a candidate only; it does not download it. Use the acquisition workflow below for accepted sources, then refresh the ledger.

## 3. Build a named collection

Create `request.json`:

```json
{
  "name": "Friday warm-up",
  "profile": "club",
  "tracks": [
    {
      "path": "/path/to/music/track.flac",
      "artist": "Artist",
      "title": "Track",
      "version": "Extended Mix"
    }
  ]
}
```

```bash
djlib --workspace /path/to/dj-workspace plan --file request.json
djlib --workspace /path/to/dj-workspace start PLAN_ID --revision 1 --key friday-v1
```

The `club` profile references existing files. `archive` copies accepted files into the managed `media/` directory. Plans snapshot profile settings. Changes to `workspace.json` don't alter an already accepted plan. Automatic genre, energy, BPM, key, and cue analysis remain later work; this version can organize explicit annotations and existing tags as described below.

A mismatch between requested labels and embedded metadata produces a review:

```bash
djlib --workspace /path/to/dj-workspace reviews list --job-id JOB_ID
djlib --workspace /path/to/dj-workspace reviews resolve REVIEW_ID \
  --revision 1 --choice use_file_metadata
```

Other choices: `accept_requested` or `skip`. An override records user choice and still does not establish acoustic identity. Some bytes can only have one catalog identity; conflicts with an already cataloged revision currently fail the item rather than rewriting history.

### Keep notes and sort explicit selections

Use the recording and byte-revision IDs returned by the catalog:

```bash
djlib --workspace /path/to/dj-workspace organize metadata RECORDING_ID \
  --asset-revision-id ASSET_REVISION_ID
djlib --workspace /path/to/dj-workspace organize get RECORDING_ID \
  --asset-revision-id ASSET_REVISION_ID
```

Metadata inspection checks the catalog hash and reads embedded BPM/key/genre/comments. Missing, malformed or conflicting BPM/key values stay unknown; a known tag is still unverified. Save supplied subjective notes in `annotations.json`, using the returned annotation revision (`0` for the first annotation):

```json
{
  "recording_id": "RECORDING_ID",
  "asset_revision_id": "ASSET_REVISION_ID",
  "revision": 0,
  "idempotency_key": "friday-notes-v1",
  "tags": ["vocals", "warm"],
  "set_role": "Warm Groove",
  "notes": "Review the vocal entrance before placing this in the opening sequence."
}
```

```bash
djlib --workspace /path/to/dj-workspace organize annotate --file annotations.json
```

Only supplied fields change; explicit null clears a field. BPM/key annotations additionally require a source (`operator` or `native_tag`) and default to `verified: false`. `native_tag` must match freshly read catalog tags; it does not read a DJ app's analysis database. Store measured values or explicit operator judgments, not invented defaults.

Create `organized.json` from accepted catalog references:

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
djlib --workspace /path/to/dj-workspace organize collection --file organized.json
```

The returned organization job is queued. Wait with `jobs wait JOB_ID --timeout 30`, inspect `jobs items JOB_ID`, then use the completed result's collection ID and per-item exclusion reasons. Collections can overlap. All active filter categories must match; values within a category are alternatives. Unknown filter evidence is excluded by default, with explicit include/error policies available. Unknown sort values remain last in either direction. Key filters match supplied labels; wheel labels sort numerically without translating between key systems. These operations do not retag originals, set native cues, or infer mood, energy or genre. A later delivery plan freezes annotations and prepares them as tags/comments in separate app working copies; inspect native display and analysis after import.

## 4. Inspect a set and select recording URLs

```bash
djlib --workspace /path/to/dj-workspace source-inspect 'https://soundcloud.com/USER/SET'
```

This retrieves bounded publisher metadata and chapters. Your assistant can interpret a published tracklist and search for individual sources. Unpublished IDs and audio recognition are unresolved capabilities. A set URL does not automatically become a batch of individual recordings.

Create `downloads.json` with actual selected recording URLs:

```json
{
  "name": "Friday web selections",
  "idempotency_key": "friday-web-v1",
  "tracks": [
    {
      "url": "https://soundcloud.com/ARTIST/RECORDING",
      "artist": "Artist",
      "title": "Track",
      "version": ""
    }
  ]
}
```

```bash
djlib --workspace /path/to/dj-workspace download --file downloads.json
```

The optional download extra, FFmpeg, and ffprobe are required. The adapter allows public HTTPS YouTube, SoundCloud, and Bandcamp URLs, excludes playlist acquisition, limits recordings to 30 minutes, and bounds staging growth to 500 MiB per active download. It requires 1.1 GiB free before each retrieval. Downloads become FLAC for app interchange; their original fidelity remains unverified. Managed FLAC copies receive your selected artist/title/version tags for readable app display. Original acquisition bytes remain in `incoming/`; the catalog records their hash, the managed copy's final hash, and the source/transformation evidence. Generated tags are supplied labels, not independent identity proof. This release does not purchase tracks, use browser cookies, or download DRM content.

For YouTube the adapter selects installed Deno, then Node (22+ required by yt-dlp). Provider failures distinguish unavailable sources, authentication requirements, rate limits, and timeouts. Cookie-based access is unsupported. One live source from each supported provider passed acquisition; those individual results do not establish universal extraction or source fidelity.

## 5. Prepare handoff

For a real DJ destination, follow [DJ delivery](DJ_DELIVERY.md). Start with `rekordbox_import` or `serato_import` for local app import/analysis; neither needs a USB or player model. Prepare a small pilot, record native import/analysis, and use `delivery verify-app`. For a device afterward, create a separate `rekordbox_usb` delivery with the exact player profile, or `serato_portable`, then complete native export, device inspection and playback. The persisted workflow keeps app working copies separate from catalog originals.

The older `export` command below remains a generic interchange handoff. It does not create or complete a target delivery.

Get the ingestion job's `result.collection_id`, then:

```bash
djlib --workspace /path/to/dj-workspace export COLLECTION_ID --key friday-export-v1
djlib --workspace /path/to/dj-workspace jobs wait EXPORT_JOB_ID --timeout 30
```

The export job checks every referenced file's current hash, then writes:

- `manifest.json`: collection, identities, measured audio properties, provenance, and readiness states.
- `collection.m3u8`: paths and human labels.
- `rekordbox.xml`: experimental interchange document with a named playlist. No fabricated BPM, key, grids, or cues.

**rekordbox 7.2.8 — tested basic path:** File → Import → Import Playlist → select `collection.m3u8`. Three generated WAV tones imported, and native waveform/key analysis appeared in Collection. Keep the referenced audio paths available. USB export still requires selecting and exporting from rekordbox to the actual device, then checking the target player.

**Experimental XML path:** select the generated XML as the XML library in preferences, open the rekordbox XML section, and import its playlist/tracks to Collection. This conveys requested labels and versions directly, without invented analysis. Schema/URI structure is tested; native XML import has not been verified.

**Serato:** use the Files panel to access the referenced music and add the tracks to a crate. The M3U is a portable reference artifact, not a verified Serato crate import. Native crate automation is planned. Managed filenames use byte hashes; selected FLAC downloads now have readable embedded display tags. The actual Serato 3.1.5 import remains unverified because the app-control session failed to capture its window. See the manual [validation procedure](VALIDATION.md).

For external references, keep original files mounted and available. For managed downloads, retain the workspace media directory. Deleting source paths after app import breaks references.

```bash
djlib --workspace /path/to/dj-workspace usb-preflight /Volumes/DJ_USB \
  --required-bytes 1000000000
```

Preflight reads storage capacity, reserves 64 MiB, and reports whether the supplied path is a mount point. It does not detect the filesystem, format the device, copy tracks, build a player database, or verify hardware compatibility.

## 6. Manage long work

```bash
djlib --workspace /path/to/dj-workspace jobs control JOB_ID pause
djlib --workspace /path/to/dj-workspace jobs control JOB_ID resume
djlib --workspace /path/to/dj-workspace jobs control JOB_ID retry
djlib --workspace /path/to/dj-workspace service stop
```

Use the same submission key after a lost response. A changed request with that key is rejected. Retry preserves successful items; cancellation preserves accepted files. Downloads are terminated at the next control checkpoint. Audio inspection/copy threads may finish an in-flight operation before their fenced result is discarded. A paused coordinator continues serving status and other queued jobs.

Stopping the service checkpoints unfinished intents. The next coordinator start resumes them. Runtime diagnostics are in `runtime/service.log`; review and redact them before sharing. Incoming and abandoned staging files are retained; garbage collection is not implemented. There is no automatic cleanup of user media.
