# djlib

[![Tests, quality, and packaging](https://github.com/uneasymusings/dj-library-tool/actions/workflows/quality.yml/badge.svg)](https://github.com/uneasymusings/dj-library-tool/actions/workflows/quality.yml)

**Check a DJ tracklist against your music. Build a crate in set order.**

A local tool for rekordbox DJs. See which exact recordings you own, find missing tracks, and prepare your next set. Use the terminal or the small light-gray review page. No account required.

[Try the demo](https://uneasymusings.github.io/dj-library-tool/preview/) · [Install](#install) · [Quick start](docs/QUICKSTART.md)

## From tracklist to crate

**A 15-second terminal walkthrough.** Check a tracklist, then inspect the crate:

[![Four Tet terminal walkthrough: check eight tracks, then inspect the four-track crate](docs/images/demo/four-tet.gif)](https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a22/gray-demo-four-tet.mp4)

```bash
djlib set four-tet.txt --name 'Four Tet / Lollapalooza 2024' --no-rekordbox
djlib crate last
```

**8 tracks → 4 owned · 2 other mixes · 2 missing.** Only the four exact matches enter this example crate. [Tracklist](docs/images/demo/four-tet.txt) · [Watch MP4](https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a22/gray-demo-four-tet.mp4)

The clip uses generated audio and illustrative BPM/key, with real CLI output paced for readability. [All five DJ walkthroughs](docs/images/demo/README.md#watch-the-walkthroughs): Avalon Emerson, Ben UFO, Four Tet, Floating Points and Skrillex.

## Install

In Terminal on macOS or Linux:

```bash
curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh
```

Choose your music folder when asked. djlib indexes it in place; your files stay where they are. On a Mac with Homebrew, missing audio helpers are installed too. The final setup check shows anything else needed. [Windows and manual installation](docs/INSTALL.md#by-hand-and-on-windows).

Then open your library:

```bash
djlib ui
```

Choose **Import playlist or tracklist**, paste your tracks or select a CSV, and **Preview selection**. Click **Check against my music**, then open the set and build its crate. If `djlib` is not found, open a new terminal. [Install help](docs/INSTALL.md) · [Optional Claude Code / Codex setup](docs/AGENTS.md).

## How it works

1. **Start with a tracklist, playlist CSV or set link.** YouTube and SoundCloud links can use Shazam to help identify songs. Unknown tracks stay IDs.
2. **Check what you own.** Exact mixes enter the crate. Other mixes and missing tracks stay visible. **Rip** and **Shop** are separate, optional choices.
3. **Prepare the set.** Read saved BPM/key from rekordbox. On macOS, import the crate and export through rekordbox, then check the USB files and playlist order.

Already use the terminal? Copy a tracklist, then run `djlib set paste` on a Mac. A saved text file works on every platform. `djlib crate last` shows the next unfinished prep step; `djlib upgrade` updates the installed tool.

The catalog and background jobs stay on your computer. Scans reuse unchanged files, and interrupted jobs can resume. Set identification contacts the chosen web providers; an hour-long mix usually takes 5–10 minutes, sometimes longer. [Workflow details](docs/QUICKSTART.md).

## For developers

Python, FastAPI and SQLite power the CLI, authenticated local service and MCP tools. Durable jobs, immutable audio revisions and explicit workflow records keep matching, analysis and USB checks traceable. The same frontend runs the local review page and the read-only public demo.

CI checks Linux, macOS and Windows on Python 3.12 and 3.13, including fresh installed packages. [Architecture](docs/ARCHITECTURE.md) · [Contributing](CONTRIBUTING.md) · [Validation](docs/VALIDATION.md) · [Security](SECURITY.md).

> **Alpha (0.1.0a23).** Native rekordbox and USB controls require macOS. Shazam is unofficial and may slow down or stop working. USB file checks do not prove CDJ playback; test your player before a gig. [Limits](docs/QUICKSTART.md#limits).

MIT licensed. Bring your own music and download rights. Not affiliated with AlphaTheta/rekordbox, Shazam/Apple, MixesDB or 1001Tracklists.
