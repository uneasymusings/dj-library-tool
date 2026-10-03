# CLI recipes

All application commands emit an envelope with `schema_version`, `ok`, `result`, `warnings`, and `error`. No `--json` option is needed. Supply an initialized workspace explicitly. Do not pass private user paths into public repository examples or commits.

```bash
djlib --workspace /path/to/workspace capabilities
djlib --workspace /path/to/workspace library "Joy Orbison"
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
