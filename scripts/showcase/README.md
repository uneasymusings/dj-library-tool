# Record the GitHub walkthroughs

The five previews use the current CLI with selected public tracklist entries and a generated demo library. They are silent, paced replays of actual CLI output. They are not fresh Shazam listens or recordings of native rekordbox/USB automation.

`sets.json` records the selection and source for each DJ. Its `owned`/`other` choices describe the illustrative library to generate; they are not claims about the user's library. The CLI decides the actual match status. Generated four-second tones carry those labels. Saved BPM/key are explicitly unverified operator annotations. The assertion checks keep exact matches in the crate and IDs unnamed.

## Capture

Use the project's Python environment:

```sh
python scripts/showcase/capture.py --output dist/showcase/captures --work /tmp/djlib-showcase-fresh
```

Choose a new, empty work location. The script refuses an existing artist folder instead of overwriting a library. The public CLI goes through the real authenticated FastAPI application and worker using an in-process HTTP transport. Matching, requests, collection jobs and terminal views are unchanged. No local socket, web source, source audio, Shazam service, native app, real music library or USB is accessed. User configuration is isolated and restored after capture. `--only ben-ufo` records one DJ.

The captured commands are `set FILE --name NAME --no-rekordbox`, `requests report last`, and `crate last`. Every command must exit successfully. Captures stay in the chosen output folder; do not commit catalog databases, generated tones, config files or personal transcripts.

## Render

Requires `ffmpeg`, `ffprobe` and `rsvg-convert` on PATH. JetBrains Mono Regular/Bold and their SIL OFL license are bundled in `fonts/`. Fontconfig uses an isolated output cache and does not install system fonts.

```sh
python scripts/showcase/render.py --captures dist/showcase/captures --output dist/showcase/rendered
```

`--only ben-ufo --poster-only` quickly renders stills for review. Full rendering creates 1440×1120 H.264 MP4s, looping 1080×840 GIFs, PNG stills and editable SVG frames. Commands are replayed with typing and output reveal; pauses are for readability and do not represent application runtime. Cuts hold up to 28 complete output lines, continuing to the ending when needed. Shopping cuts hold the results before saved-file paths and shell-open hints; original captures retain the complete output.

Review the result and prep PNGs, decode every movie/GIF, check dimensions/duration and verify the displayed demo counts against the captures. Copy GIFs and posters into `docs/images/demo/`. Upload `demo-*.mp4` and `SHOWCASE-SHA256SUMS` to the existing release used by the captures, then check the public download hashes. Do not replace the package assets or their `SHA256SUMS`.

These generators are development tools. The CLI/MCP runtime and package version are unchanged by a media refresh.
