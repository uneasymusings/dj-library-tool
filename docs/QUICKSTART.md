# Quickstart

This is an experimental source release. Follow the [status](STATUS.md) and use a separate workspace before attempting a personal-library workflow.

## 1. Choose a workspace

```bash
uv run djlib --workspace /path/to/dj-workspace init --allow-root /path/to/music
uv run djlib --workspace /path/to/dj-workspace doctor
```

The workspace stores its catalog, runtime token, media, and exports. Its directory must be empty on first initialization. Repeat initialization returns the existing configuration; it does **not** silently add new allowed roots. To allow another music folder, stop the coordinator and edit `allowed_roots` in `workspace.json` to include its resolved absolute path. Symlinks outside those roots are rejected.

Always specify `--workspace` before the subcommand, or set `DJLIB_WORKSPACE`. The default is `~/.local/share/djlib/default`; initialization is explicit. `doctor` reports FFmpeg, ffprobe, optional yt-dlp, and Deno availability, without starting the coordinator.

## 2. Index owned audio

```bash
uv run djlib --workspace /path/to/dj-workspace scan /path/to/music --key music-scan-v1
```

Save `result.job_id` from the response:

```bash
uv run djlib --workspace /path/to/dj-workspace jobs wait JOB_ID --timeout 30
uv run djlib --workspace /path/to/dj-workspace jobs items JOB_ID
uv run djlib --workspace /path/to/dj-workspace library "Artist"
```

Scans index WAV, MP3, FLAC, AIFF, and M4A files in place, using embedded artist/title tags or filename fallbacks. A hash proves byte identity, not musical identity. Untagged scan entries are explicitly labeled `Unknown artist`; scans do not identify them acoustically. Large scans are limited to 10,000 supported files; choose subfolders for larger libraries. Original files are never renamed or retagged.

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
uv run djlib --workspace /path/to/dj-workspace plan --file request.json
uv run djlib --workspace /path/to/dj-workspace start PLAN_ID --revision 1 --key friday-v1
```

The `club` profile references existing files. `archive` copies accepted files into the managed `media/` directory. Plans snapshot profile settings. Changes to `workspace.json` don't alter an already accepted plan. This release organizes named collections; genre, energy, BPM, key, and cue analysis are later work.

A mismatch between requested labels and embedded metadata produces a review:

```bash
uv run djlib --workspace /path/to/dj-workspace reviews list --job-id JOB_ID
uv run djlib --workspace /path/to/dj-workspace reviews resolve REVIEW_ID \
  --revision 1 --choice use_file_metadata
```

Other choices: `accept_requested` or `skip`. An override records user choice and still does not establish acoustic identity. Some bytes can only have one catalog identity; conflicts with an already cataloged revision currently fail the item rather than rewriting history.

## 4. Inspect a set and select recording URLs

```bash
uv run djlib --workspace /path/to/dj-workspace source-inspect 'https://soundcloud.com/USER/SET'
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
uv run djlib --workspace /path/to/dj-workspace download --file downloads.json
```

The optional download extra, FFmpeg, and ffprobe are required. The adapter allows public HTTPS YouTube, SoundCloud, and Bandcamp URLs, excludes playlist acquisition, limits recordings to 30 minutes, and bounds staging growth to 500 MiB per active download. It requires 1.1 GiB free before each retrieval. Downloads become FLAC for app interchange; their original fidelity remains unverified. Source URL and transformation evidence are retained in catalog properties and export manifests. This release does not purchase tracks, use browser cookies, or download DRM content.

## 5. Prepare handoff

Get the ingestion job's `result.collection_id`, then:

```bash
uv run djlib --workspace /path/to/dj-workspace export COLLECTION_ID --key friday-export-v1
uv run djlib --workspace /path/to/dj-workspace jobs wait EXPORT_JOB_ID --timeout 30
```

The export job checks every referenced file's current hash, then writes:

- `manifest.json`: collection, identities, measured audio properties, provenance, and readiness states.
- `collection.m3u8`: paths and human labels.
- `rekordbox.xml`: experimental interchange document with a named playlist. No fabricated BPM, key, grids, or cues.

**rekordbox:** select the generated XML as the XML library in preferences, open the rekordbox XML section, import the playlist/tracks to Collection, analyze, and export from rekordbox to the selected device. Labels and preference locations depend on the app version; the exact local procedure is awaiting app validation.

**Serato:** use the Files panel to access the referenced music and add the tracks to a crate. The M3U is a portable reference artifact, not a verified Serato crate import. Native crate automation is planned. Managed filenames currently use byte hashes and untagged web audio can display those filenames in Serato; a safe metadata/filename preparation pass is still needed for a polished Serato handoff. rekordbox XML carries the supplied labels directly.

For external references, keep original files mounted and available. For managed downloads, retain the workspace media directory. Deleting source paths after app import breaks references.

```bash
uv run djlib --workspace /path/to/dj-workspace usb-preflight /Volumes/DJ_USB \
  --required-bytes 1000000000
```

Preflight reads storage capacity, reserves 64 MiB, and reports whether the supplied path is a mount point. It does not detect the filesystem, format the device, copy tracks, build a player database, or verify hardware compatibility.

## 6. Manage long work

```bash
uv run djlib --workspace /path/to/dj-workspace jobs control JOB_ID pause
uv run djlib --workspace /path/to/dj-workspace jobs control JOB_ID resume
uv run djlib --workspace /path/to/dj-workspace jobs control JOB_ID retry
uv run djlib --workspace /path/to/dj-workspace service stop
```

Use the same submission key after a lost response. A changed request with that key is rejected. Retry preserves successful items; cancellation preserves accepted files. Downloads are terminated at the next control checkpoint. Audio inspection/copy threads may finish an in-flight operation before their fenced result is discarded. A paused coordinator continues serving status and other queued jobs.

Stopping the service checkpoints unfinished intents. The next coordinator start resumes them. Runtime diagnostics are in `runtime/service.log`; review and redact them before sharing. Incoming and abandoned staging files are retained; garbage collection is not implemented. There is no automatic cleanup of user media.
