# Quickstart

Install djlib with [INSTALL](INSTALL.md); [status](STATUS.md) has the test evidence and limits. Commands use the workspace `djlib init` created and remembered; add `--workspace PATH` (or set `DJLIB_WORKSPACE`) only for another one. In a terminal, every command prints a readable view; piped or with `--json` it prints the JSON envelope. Contributors can prefix commands with `uv run` in their checkout.

## 1. Workspace and music

```bash
djlib init --allow-root ~/Music
djlib scan
djlib doctor
djlib status
```

The workspace holds the catalog, a runtime token, managed media and exports; its folder must be empty the first time. Add another folder later with `djlib roots add /path/to/folder` (`init` doesn't add folders to an existing workspace and says so with `ROOTS_NOT_UPDATED`). Adding access never moves music, and symlinks outside allowed folders are rejected.

`scan` indexes WAV, MP3, FLAC, AIFF and M4A files in place, using their artist/title tags or `Artist - Title` file names. It never renames or retags files, and rescans decode only new bytes. A hash proves byte identity, not musical identity; untagged files keep a provisional identity. Up to 10,000 files per scan; pass subfolders for larger libraries.

## 2. A set

```bash
djlib set tracklist.txt --no-rekordbox     # what you own and the crate, nothing else
djlib set tracklist.txt                    # … and a rekordbox playlist (macOS)
djlib set tracklist.txt --usb              # … and your USB stick, verified from the stick
djlib set 'https://soundcloud.com/USER/SET' --fetch
```

See the [README](../README.md#how-it-works) for what each step does and [the CLI reference](../skills/dj-library/references/cli.md#one-command-for-a-set) for the result fields. On Linux and Windows the rekordbox step is skipped with the reason shown; the crate is still built.

### Find saved work and page the catalog

```bash
djlib library --query "Artist" --limit 100
djlib library --query "Artist" --limit 100 --after NEXT_CURSOR
djlib collections --query "Warm"
djlib requests list --query "Friday"
djlib delivery list --query "Friday"
djlib jobs list --query "Friday"
```

Continue with each reply's `next_cursor` and the same query until it is null. Rows show recorded locations, not freshly checked availability. A collection's app/device state is `not_tracked_here`: rekordbox pushes are listed by `djlib status`, and working-copy deliveries keep their own evidence.

### Track exact requests

`set` saves its tracklist as a request list. To work with one directly, save `wanted.json`:

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
djlib requests create --file wanted.json         # or --text tracklist.txt
djlib requests get REQUEST_ID
djlib requests refresh REQUEST_ID --revision CURRENT_REVISION
djlib requests collect REQUEST_ID
djlib requests report REQUEST_ID --revision CURRENT_REVISION
```

Matching uses normalized artist/title/version labels; another mix never substitutes, and "(Original Mix)" counts as no version. Several matching byte revisions stay ambiguous until you choose. Reads show saved evidence; create, refresh and resolve check files. Unknown IDs stay explicit. Selecting a source with `requests resolve` records a candidate only; it doesn't download it. Details: [requests](../skills/dj-library/references/requests.md).

## 3. Collections from local files

Create `request.json`:

```json
{
  "name": "Friday warm-up",
  "profile": "club",
  "tracks": [
    {"path": "/path/to/music/track.flac", "artist": "Artist", "title": "Track", "version": "Extended Mix"}
  ]
}
```

```bash
djlib plan --file request.json
djlib start PLAN_ID --revision 1 --key friday-v1
djlib reviews list --job-id JOB_ID
djlib reviews resolve REVIEW_ID --revision 1 --choice use_file_metadata
```

`club` references existing files; `archive` copies accepted files into the workspace's `media/`. Requested labels that disagree with the file's tags produce a review: `accept_requested`, `use_file_metadata` or `skip`. A choice records your decision; it doesn't establish acoustic identity.

### Keep notes and sort explicit selections

```bash
djlib organize metadata RECORDING_ID --asset-revision-id ASSET_REVISION_ID
djlib organize get RECORDING_ID --asset-revision-id ASSET_REVISION_ID
djlib organize annotate --file annotations.json
djlib organize collection --file organized.json
djlib rekordbox sync                  # BPM and cues from rekordbox's analysis files
djlib rekordbox pull                  # adds key through one XML export (macOS)
```

Embedded BPM/key/genre are read as unverified evidence; missing or conflicting values stay unknown. Annotations (notes, tags, set role, energy, BPM/key with their source) patch only the fields you supply, never retag originals and never set cues. rekordbox's analysis arrives as `source: rekordbox_analysis`, unverified, and values you set are kept. `organize collection` builds an ordered, filtered crate (BPM range, keys, genres, tags, roles) and reports what it excluded; unknown values are excluded unless you choose `include`. JSON examples: [organize](../skills/dj-library/references/organize.md).

### Reconcile a file edited outside djlib

Don't rescan changed bytes and assume old crates now point at them. Submit `djlib reconcile --file reconciliation.json` with the old revision and the current file's SHA-256 (`tag_only` for tag edits, `replace_audio` for new audio), then wait for the job. Old crates and notes stay pinned to the old bytes; see [organize](../skills/dj-library/references/organize.md#a-file-changed-on-disk).

## 4. Set links and downloads

```bash
djlib source-inspect 'https://soundcloud.com/USER/SET'
djlib download --file downloads.json
```

`source-inspect` reads a YouTube or SoundCloud set's description and chapters; `set URL` turns them into a tracklist and adds listeners' comments around each ID as hints. 1001Tracklists only serves browsers: copy its tracklist into a file. Nothing in djlib recognizes audio.

`set --fetch` searches YouTube and SoundCloud for missing songs and downloads clear matches after you confirm. `download --file` takes selected URLs:

```json
{
  "name": "Friday web selections",
  "idempotency_key": "friday-web-v1",
  "tracks": [
    {"url": "https://soundcloud.com/ARTIST/RECORDING", "artist": "Artist", "title": "Track", "version": ""}
  ]
}
```

Downloads need the `download` extra and FFmpeg; YouTube also needs Deno or Node 22+. Public HTTPS YouTube, SoundCloud and Bandcamp recordings only, no playlists, up to 30 minutes each. Each download is saved as MP3 (copied when the source already is MP3) and tagged with your artist/title/version; its source quality stays unverified. The downloaded file stays untouched in `incoming/`; the catalog records its hash, the tagged copy's hash and the conversion. djlib doesn't buy tracks, use browser cookies or download DRM content, and you are responsible for having the rights.

## 5. Into rekordbox, onto USB

```bash
djlib rekordbox push COLLECTION_ID --when-idle 60
djlib rekordbox usb COLLECTION_ID
```

`push` imports a crate as a playlist of your original files through rekordbox's own File > Import > Import Playlist (macOS, with the Accessibility permission). `usb` waits for you to click the playlist, runs Playlist > Export Playlist to your stick and checks every file there against your originals, in order. Playback on the player itself is not verified: test the stick before a gig.

Other handoffs:

- `djlib export COLLECTION_ID --key friday-export-v1` writes a hash-checked `manifest.json`, `collection.m3u8` and an experimental `rekordbox.xml` (no invented BPM, key, grids or cues).
- **Serato:** import the referenced files through the Files panel and add them to a crate. Serato working copies are supported in [DJ delivery](DJ_DELIVERY.md) but untested with real music.
- [DJ delivery](DJ_DELIVERY.md) also covers separate, tagged working copies for rekordbox, with guided native-app and USB checks.
- `djlib usb-preflight /Volumes/DJ_USB --required-bytes 1000000000` only reads free space. djlib never formats, writes or ejects a USB stick.

Keep referenced files where they are: rekordbox and Serato point at them.

## 6. Long work

```bash
djlib jobs wait JOB_ID --timeout 30
djlib jobs items JOB_ID --state failed
djlib jobs control JOB_ID pause|resume|cancel|retry
djlib service stop
```

Reuse the same `--key` after a lost reply; a changed request with that key is rejected. Retry keeps successful items, and cancelling keeps accepted files. Stopping the service keeps unfinished work, which resumes on the next start. Logs are in the workspace's `runtime/service.log`; redact them before sharing. Staging and incoming files are not cleaned up automatically. Errors and recovery: [troubleshooting](TROUBLESHOOTING.md).
