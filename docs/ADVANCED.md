# Advanced commands

For power users, scripts and assistants. `djlib --help` shows the everyday commands; everything here still works. Run any command with `--help` for its options. The everyday guide is the [quick start](QUICKSTART.md).

## Shortcuts

- `djlib library QUERY` searches your tracks (`djlib search QUERY` does the same).
- A group on its own does the obvious thing: `djlib requests` lists your sets, `djlib shazam` shows `shazam status`, `djlib roots` lists your music folders and `djlib jobs` lists recent jobs.
- `djlib crate` with no name shows your newest crate.
- Sets and crates take a name (any case), `last` for the newest, or the first 6 or more characters of an ID: `djlib requests get|collect|refresh|report|resolve SET`, `djlib crate CRATE`, `djlib rekordbox push|usb CRATE`, `djlib export CRATE` and `djlib delivery plan --collection CRATE`. `requests refresh`, `report` and `resolve` use the set's current revision unless you pass `--revision`.
- `--json` and `-w/--workspace PATH` work before or after the command name, though `djlib --help` doesn't list them. `DJLIB_WORKSPACE` does the same as `--workspace`.

## Terminal views and JSON

In a terminal every command prints a readable view, with live progress for long jobs and next steps you can paste. Piped, captured, or with `--json`, every command prints the same JSON envelope instead (`ok`, `result`, `error`), one per command. `DJLIB_OUTPUT=pretty` forces the readable view. Long jobs report progress events on stderr in JSON mode. The [CLI reference](../skills/dj-library/references/cli.md) lists the result fields.

Commands that start a background job take `--key TOKEN` in scripts: a retry token you choose, so retrying never runs the job twice. Reuse the same `--key` after a lost reply; a changed request with that key is rejected. A terminal makes one up for you.

## Your music folders and the index

```bash
djlib init ~/Music '/Volumes/DJ Drive/Music'   # set up, or add folders to your setup
djlib roots add '/Volumes/Other Drive'         # the same, for an existing setup
djlib scan                                     # index all your music folders
djlib scan '/Volumes/DJ Drive/Music/New'       # just one folder
```

The workspace holds the catalog, a runtime token, managed media and exports; `djlib init` makes one at `~/.local/share/djlib/default` and remembers it (`djlib use PATH` picks another). Adding a folder never moves music, and symlinks outside your folders are rejected.

`scan` indexes WAV, MP3, FLAC, AIFF and M4A files in place, using their artist/title tags or `Artist - Title` file names. It never renames or retags files, and rescans decode only new bytes. A hash proves byte identity, not musical identity; untagged files keep a provisional identity. Up to 100,000 files per scan; pass subfolders for larger libraries.

## How listening to a set works

`djlib identify 'LINK'` (and `djlib set 'LINK'` when the tracklist is missing, has IDs or names too few songs for the set's length: one, or fewer than one per 12 minutes) reads the set's description, chapters and listener comments, looks the set up on MixesDB, downloads the audio and sends a 12-second clip every 30 seconds to song recognition (every minute when a timed tracklist says what plays). It listens again where songs change, then merges everything into one timed tracklist saved in the workspace's `sets/` folder. The audio is deleted afterwards unless `DJLIB_KEEP_SET_AUDIO=1`. `djlib identify 'LINK' --again` listens again even when djlib heard the set before.

`DJLIB_MIXESDB=off` (or `0`, `false`, `no`) stops djlib asking MixesDB, for `djlib set`, `djlib identify` and assistants alike; a set djlib listened to before keeps the MixesDB tracklist it merged then. djlib's background service reads it when it starts: run `djlib service stop`, and the next command starts it with the setting.

When Shazam hears a listed song clearly but the tracklist misspells its artists (“Armand Val Helden”), djlib uses the spelling Shazam heard, so your files are found, and notes how the tracklist wrote it; and if your file is tagged the way a tracklist spells it (MixesDB's, or one you pasted), it still counts as owned (“Owned under another spelling”), for the same exact mix. A tracklist written title first (“Desert Hike - Tibi Dabo”) agrees with what Shazam heard the other way round, and the notes say so; lines Shazam didn't hear stay as written. A song heard once that fits nowhere is listed under “Heard once, not placed”, not as a track. `djlib source-inspect 'LINK'` reads only the description and chapters.

Shazam is the default, used the way the Shazam app uses it, without an official API. AudD and ACRCloud are licensed options with your own key: see [song recognition](INSTALL.md#song-recognition). `djlib identify 'LINK' --recognizer audd` picks one for a single run.

## Shazam sources

`djlib set shazam:apple-music` reads Apple Music's “My Shazam Tracks” playlist (Mac). `djlib set shazam:PATH` reads an iPhone backup folder, a `ShazamDataModel.sqlite` or a CSV. A CSV with Shazam's own columns works as a plain file too; a CSV with only Artist and Title columns is read as a song list, so use `shazam:FILE` to force it. `djlib shazam list --since 2026-10-01 --limit 100` shows the songs, newest first; `--from` reads another source.

## Sets by hand

`djlib set` saves its tracklist as a set (a request list). To work with one directly, save `wanted.json`:

```json
{
  "name": "Friday requests",
  "idempotency_key": "friday-requests-v1",
  "items": [
    {"artist": "Artist", "title": "Track", "version": "Extended Mix"},
    {"kind": "unknown", "label": "ID", "timestamp": "00:47:12"}
  ]
}
```

```bash
djlib requests create --file wanted.json       # or --text tracklist.txt
djlib requests get last
djlib requests refresh last                     # check again, e.g. after a scan
djlib requests collect last                     # the crate: owned songs in set order
djlib requests report last                      # the shopping list
djlib requests resolve last ITEM_ID --file choice.json
```

Matching uses normalized artist/title/version labels; another mix never substitutes, and “(Original Mix)” counts as no version. “and” matches “&” in titles, and a remixer's country tag inside a mix name (“Antdot & Maz (BR) Remix”) is ignored, but a file spelled exactly as asked wins. A confirmed song also matches a file tagged with a tracklist's credits for it (“Owned under another spelling”). A file with the same title and mix under a shorter or longer artist credit (“Robert Glasper” for “Robert Glasper Experiment”) is “maybe yours”: off the shopping list, and owned once you pick Use this on the review page. Several matching files stay “pick a copy” until you choose. Reads show saved evidence; create, refresh and resolve check files. IDs stay IDs. Picking a web source with `requests resolve` records a candidate only; it doesn't download it. Details: [requests](../skills/dj-library/references/requests.md).

## Crates from local files

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

`club` references existing files; `archive` copies accepted files into the workspace's `media/`. Labels that disagree with the file's tags produce a review: `accept_requested`, `use_file_metadata` or `skip`. A choice records your decision; it doesn't establish acoustic identity.

## Keep notes and sort explicit selections

```bash
djlib organize metadata RECORDING_ID --asset-revision-id ASSET_REVISION_ID
djlib organize get RECORDING_ID --asset-revision-id ASSET_REVISION_ID
djlib organize annotate --file annotations.json
djlib organize collection --file organized.json
djlib rekordbox sync                  # BPM and cues from rekordbox's analysis files
djlib rekordbox pull                  # adds key through one XML export (macOS)
```

Run `djlib rekordbox sync` once; after that BPM and cues stay current in the background. Embedded BPM/key/genre are read as unverified evidence; missing or conflicting values stay unknown. Annotations (notes, tags, set role, energy, BPM/key with their source) patch only the fields you supply, never retag originals and never set cues. rekordbox's analysis arrives as `source: rekordbox_analysis`, unverified, and values you set are kept. `organize collection` builds an ordered, filtered crate (BPM range, keys, genres, tags, roles) and reports what it left out; unknown values are left out unless you choose `include`. JSON examples: [organize](../skills/dj-library/references/organize.md). `djlib import-rekordbox FILE` brings BPM and key from a rekordbox collection XML.

## A file edited outside djlib

Don't rescan changed bytes and assume old crates now point at them. Submit `djlib reconcile --file reconciliation.json` with the old revision and the current file's SHA-256 (`tag_only` for tag edits, `replace_audio` for new audio), then wait for the job. Old crates and notes stay pinned to the old bytes; see [organize](../skills/dj-library/references/organize.md#a-file-changed-on-disk).

## Downloads

`djlib set 'LINK' --fetch` searches YouTube and SoundCloud for missing songs and downloads clear matches after you confirm (`--yes` skips the question). Official uploads come first; previews, live versions, full sets and remixes you didn't ask for are skipped, and unclear ones are listed with the best guess. `djlib download --file` takes the URLs you picked:

```json
{
  "name": "Friday web selections",
  "idempotency_key": "friday-web-v1",
  "tracks": [
    {"url": "https://soundcloud.com/ARTIST/RECORDING", "artist": "Artist", "title": "Track", "version": ""}
  ]
}
```

Downloads need the `download` extra and FFmpeg; YouTube also needs Deno or Node 22+. Public HTTPS YouTube, SoundCloud and Bandcamp recordings only, no playlists, up to 30 minutes each. Each is saved as MP3 (copied when the source already is MP3), tagged with your artist/title/version and marked “Web rip from … (stream quality) — buy before playing out” in its comment, which rekordbox shows in Comments. The original download stays untouched in `incoming/`; the catalog records both hashes and the conversion. djlib doesn't buy tracks, use browser cookies or download DRM content, and you are responsible for having the rights.

## rekordbox, Serato and USB by hand

- `djlib rekordbox push CRATE [--when-idle 60] [--verify]`: put crates into rekordbox; `--verify` also checks members and key through one XML export.
- `djlib rekordbox usb CRATE [--device /Volumes/DJ_USB]`: export to the stick through rekordbox and check it.
- `djlib export CRATE --key friday-export-v1` writes a hash-checked `manifest.json`, `collection.m3u8` and an experimental `rekordbox.xml` (no invented BPM, key, grids or cues).
- **Serato:** import the referenced files through the Files panel and add them to a crate. Serato working copies are supported in [DJ delivery](DJ_DELIVERY.md) but untested with real music.
- `djlib delivery`: separate, tagged working copies for rekordbox or Serato with guided app and USB checks (`plan`, `prepare`, `bind-device`, `observe`, `verify-app`, `verify-device`, `inspect-native-xml`). See [DJ delivery](DJ_DELIVERY.md).
- `djlib usb-preflight /Volumes/DJ_USB --required-bytes 1000000000` only reads free space. djlib never formats, writes or ejects a USB stick.

Keep referenced files where they are: rekordbox and Serato point at them.

## The review page

`djlib ui` opens Sets · Crates · Library. The older pages are still there at `#/overview`, `#/deliveries` and `#/jobs`. The Sure column uses the terminal's words: “◐ likely”, “! weak” in amber, “✗ conflict” in red, nothing for confirmed, nor for an ID (its Own column says ID). Times link to that moment of the set when djlib knows its link (every set `djlib set` checks from a link). A set's crate opens with one click, with its rekordbox and USB commands. A list over 60 tracks opens on the songs to act on, 50 at a time, and a Shazam list reads newest first. A song marked “Not in stores yet” gets no store search and isn't in “Copy N to buy”. An untimed line in a timed set shows “—”. `djlib ui --no-open` prints the link without opening it; the link holds a private token, so keep it to yourself.

## Paging and saved work

```bash
djlib library --query "Artist" --limit 100
djlib library --query "Artist" --limit 100 --after NEXT_CURSOR
djlib crates --query "Warm"
djlib requests list --query "Friday"
djlib delivery list --query "Friday"
djlib jobs list --query "Friday"
```

Continue with each reply's `next_cursor` and the same query until it is null. Rows show recorded locations, not freshly checked availability.

## Long work

```bash
djlib jobs wait JOB_ID --timeout 30
djlib jobs items JOB_ID --state failed
djlib jobs control JOB_ID pause|resume|cancel|retry
djlib service stop
```

Retry keeps successful items, and cancelling keeps accepted files. Stopping the service keeps unfinished work, which resumes on the next start. Logs are in the workspace's `runtime/service.log`; redact them before sharing. Staging and incoming files are not cleaned up automatically.

## Other hidden commands

- `djlib collections` and `djlib collection CRATE`: the old names of `djlib crates` and `djlib crate`.
- `djlib sets`: another name for `djlib requests`, as in `djlib sets get last` and `djlib sets report last`.
- `djlib source-inspect 'LINK'`: read a set's description and chapters.
- `djlib demo`: try the whole workflow on three generated tones: `djlib --workspace ./demo demo`, then add `--workspace ./demo` to the commands it suggests.
- `djlib schemas`: print the JSON input schemas.
- `djlib capabilities`: list implemented and planned capabilities.
- `djlib version`: print the version as JSON (`djlib --version` prints it as text).
- `djlib completion [SHELL] [--install]`: print or install shell completion for bash, zsh, fish or PowerShell.
