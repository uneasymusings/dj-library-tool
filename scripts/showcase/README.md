# Record the GitHub walkthroughs

The five previews use the current CLI with selected public tracklist entries and a generated demo library. They are silent, paced replays of actual CLI output. They are not fresh Shazam listens or recordings of native rekordbox/USB automation.

`sets.json` records the selection and source for each DJ. Its `owned`/`other` choices describe the illustrative library to generate; they are not claims about the user's library. The CLI decides the actual match status. Generated four-second tones carry those labels. Saved BPM/key are explicitly unverified operator annotations. The assertion checks keep exact matches in the crate and IDs unnamed.

## Capture

Use the project's Python environment:

```sh
python scripts/showcase/capture.py --output dist/showcase/captures --work /tmp/djlib-showcase-fresh
```

Choose a new, empty work location. The script refuses an existing artist folder instead of overwriting a library. The public CLI goes through the real authenticated FastAPI application and worker using an in-process HTTP transport. Matching, requests, collection jobs and terminal views are unchanged. No local socket, web source, source audio, Shazam service, native app, real music library or USB is accessed. User configuration is isolated and restored after capture. `--only ben-ufo` records one DJ.

The captured commands are `set FILE --name NAME --no-rekordbox` and `crate last`. Every command must exit successfully. Captures stay in the chosen output folder; do not commit catalog databases, generated tones, config files or personal transcripts.

## Render

Requires `ffmpeg`, `ffprobe` and `rsvg-convert` on PATH. JetBrains Mono Regular/Bold and their SIL OFL license are bundled in `fonts/`. Fontconfig uses an isolated output cache and does not install system fonts.

```sh
python scripts/showcase/render.py --captures dist/showcase/captures --output dist/showcase/rendered
```

`--only ben-ufo --poster-only` quickly renders stills for review. Full rendering creates 1280×800 H.264 MP4s, looping 1080×676 GIFs, PNG stills and editable SVG frames. Commands and captured stdout fill the terminal viewport, with typing and output reveal. There are no title cards, chapter bars, branding panels or overlays. Pauses aid readability and do not represent application runtime. Cuts preserve complete lines. The two commands show ownership and crate prep. Rip and Shop remain optional next steps; no provider search or source-audio download is performed.

Review the result and prep PNGs, decode every movie/GIF, check dimensions/duration and verify the displayed demo counts against the captures. Copy GIFs and posters into `docs/images/demo/`. Upload `gray-demo-*.mp4` and `GRAY-SHA256SUMS` to the existing release used by the captures, then check the public download hashes. Do not replace the package assets or their `SHA256SUMS`.

These generators are development tools; the source version is recorded in each capture.

## Public UI preview

`python scripts/showcase/build_preview.py --captures dist/showcase/captures` exports the same production frontend with a read-only data transport. The example data comes from these real API captures; workstation paths are replaced with `/demo/` paths. No audio or credentials are published.

For UI-only edits, run `python scripts/showcase/build_preview.py --refresh-ui` to keep the existing demo catalog. `--check` verifies that the public assets match the production frontend; CI runs it. GitHub Pages serves `docs/preview/`. The public transport rejects every write with instructions to run the local app.
