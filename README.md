# DJ Library Tool

[![Tests, quality, and packaging](https://github.com/uneasymusings/dj-library-tool/actions/workflows/quality.yml/badge.svg)](https://github.com/uneasymusings/dj-library-tool/actions/workflows/quality.yml)

**From a tracklist to a verified USB stick, using the music you already own.**

`djlib` is a local-first prep tool for rekordbox DJs. Give it a set's tracklist and it tells you which songs you own, which are missing and where you only have a different mix. It builds the crate in set order, puts it into rekordbox and, with one click from you, onto your USB stick. Then it reads the stick back to prove every track is there, in order, byte for byte. Use it from the terminal, or let Claude Code or Codex drive it through MCP. Your music and catalog never leave your computer.

```text
$ djlib set Friday.txt --usb
→ In rekordbox, click the playlist Friday — Warm-up. djlib exports it as soon as it is selected.
✓ Friday — Warm-up  12 of 15 songs owned
  Crate      12 tracks, in set order
  rekordbox  ✓ playlist “Friday — Warm-up” imported
  USB        ✓ RICARDO_AM: 12 of 12 in the player's library, in order and byte for byte

Not in the crate (3)
 #   Status         Requested                You own
 4   ✗ missing      Lumen - Halo (Dub)       Radio Edit
 9   ✗ missing      Someone - Not Owned
13   ○ unknown ID   ID - ID @ 41:20
```

> **Experimental alpha, 0.1.0a10.** Checked on macOS, Linux and Windows with Python 3.12 and 3.13, and live against rekordbox 7.2.19 with a real library and USB stick. Playback on CDJ/XDJ hardware is not verified by the tool, so test your stick before a gig. Evidence and limits are in [status](docs/STATUS.md).

## Quick start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```bash
uv tool install --python 3.13 'dj-library-tool[download] @ https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a10/dj_library_tool-0.1.0a10-py3-none-any.whl'
djlib init --allow-root ~/Music      # creates and remembers your workspace
djlib scan                           # indexes your music in place
djlib set tracklist.txt --usb        # tracklist → crate → rekordbox → USB
djlib status                         # library, request lists, crates, next steps
```

A tracklist is plain text with one `Artist - Title (Mix)` per line. Numbering, timestamps and `[Label]` suffixes are ignored, and a heading line names the set. FFmpeg/ffprobe are needed for compressed formats. rekordbox automation needs macOS and a one-time permission: System Settings → Privacy & Security → Accessibility → your terminal app. The catalog itself also runs on Linux and Windows. See [installation](docs/INSTALL.md) for PATH, updates and Windows.

## How it works

| Step | Command | What happens |
| --- | --- | --- |
| All at once | `djlib set tracklist.txt [--usb]` | Runs the steps below. Without `--usb` it stops at the rekordbox playlist. Running it again reuses the list, crate and playlist. |
| Index | `djlib scan` | Reads tags, or “Artist - Title” file names, in place. Nothing is moved or retagged. Rescans only decode new bytes. |
| Ask | `djlib requests create --text tracklist.txt` | Each line becomes owned, missing, other version owned, or unknown ID. Matching uses exact artist/title/version labels and is hash-checked; a different mix is never swapped in. |
| Crate | `djlib requests collect ID` | Owned songs become a collection in set order; missing ones stay listed (`requests report` saves them). |
| rekordbox | `djlib rekordbox push ID [--when-idle 60]` | Imports the crate as a playlist of your original files through rekordbox's File > Import > Import Playlist, so existing analysis and cues are kept. Takes a few seconds; with `--when-idle`, only once you've stepped away. |
| Analysis | `djlib rekordbox sync` (automatic) | BPM and cue counts are read from rekordbox's own analysis files in the background, with no window. `rekordbox pull` adds musical key through one brief XML export. |
| USB | `djlib rekordbox usb ID` | You click the playlist once. djlib confirms it is the right one and runs Playlist > Export Playlist > your stick. It then reads the playlist from the stick's device library (`export.pdb`, what CDJs load) and checks each entry, in order, against your files' SHA-256. |

### How djlib drives rekordbox

- **Only rekordbox's own menus and dialogs.** djlib never reads or writes rekordbox's database. Before any keystroke it checks that rekordbox and the expected dialog have focus, and otherwise cancels.
- **One click for USB.** rekordbox's browser can't be scripted, so you select the playlist when asked. djlib reads which one is selected from rekordbox's export dialog and exports nothing else.
- **Your screen stays yours.** Analysis is read from files in the background. `--when-idle` defers pushes until you're away, and a locked screen just means "wait".
- **Read-back, not trust.** Pushes are confirmed from rekordbox's playlist menu, or from its XML export with `--verify`. USB exports are confirmed from the stick itself. djlib never writes the stick; rekordbox does.

## In the terminal, for scripts, and in the browser

In a terminal every command prints a readable view, with live progress for long jobs and copy-pasteable next steps. Piped, captured, or with `--json`, every command prints the same stable JSON envelope instead. `djlib ui` opens a private local review page with library search and BPM/key, request lists with version picks, crate building, and delivery checklists.

## Use with an AI assistant

The package ships an MCP server (42 tools) and a [skill](skills/dj-library/SKILL.md) that teaches the workflow, including computer etiquette: idle-time pushes, batching, no UI polling. Register it with your assistant:

```bash
claude mcp add --transport stdio djlib -- "$(command -v djlib)" mcp serve
codex mcp add djlib -- "$(command -v djlib)" mcp serve
```

Or generate a separate trial session with `djlib setup-agent --output ~/djlib-session`, then run `python3 ~/djlib-session/launch.py claude`. Then ask, for example:

> Here's tonight's tracklist. Check what I own, tell me what's missing and which other versions I have, and put the set on my USB through rekordbox.

The rekordbox and USB steps run through the CLI on your Mac. The assistant tells you when to click the playlist. See [agent setup](docs/AGENTS.md).

## More

- **Optional web recordings.** With the `download` extra, selected public YouTube, SoundCloud or Bandcamp recordings can be fetched as durable per-track jobs, labelled as unverified source quality. See [quickstart](docs/QUICKSTART.md).
- **Delivery workflows.** Separate, tagged working copies with guided native-app and USB checks for when you don't want to export your originals: [DJ delivery](docs/DJ_DELIVERY.md). Serato working copies are supported there but untested with real music.
- **Try it without your music.** `djlib --workspace ./demo demo` builds a collection from three generated tones.

## Architecture

```mermaid
flowchart LR
    A[Claude Code / Codex + skill] --> B[MCP stdio]
    C[CLI: terminal views or JSON] --> D[Authenticated loopback coordinator]
    B --> D
    C --> R[rekordbox via its menus, macOS]
    D --> E[SQLite catalog + durable jobs]
    D --> F[Background worker: scans, exports, analysis sync]
    R --> U[USB stick: read back from export.pdb]
```

One coordinator owns a workspace and exits after 30 idle minutes when it was started implicitly. Recordings, immutable byte revisions, file locations and collection membership are separate entities, so a renamed or retagged file never silently changes a crate. Read the [architecture](docs/ARCHITECTURE.md), the [CLI/API contract](docs/CONTRACT.md), [coverage](docs/COVERAGE.md) and the [product plan](PLAN.md).

## Development

Requires Python 3.12+ and uv.

```bash
git clone https://github.com/uneasymusings/dj-library-tool.git && cd dj-library-tool
uv sync --locked --extra download
uv run pytest -q
uv run ruff check src scripts tests && uv run ruff format --check src scripts tests
```

CI runs tests, lint, formatting and packaging on Linux, macOS and Windows with Python 3.12 and 3.13; tagged releases are built and checked before publishing. See [validation](docs/VALIDATION.md), [contributing](CONTRIBUTING.md) and [security](SECURITY.md).

MIT licensed. Music stays local; you supply your own sources and download rights. Independent project, not affiliated with AlphaTheta/rekordbox, Serato, or the supported assistant hosts.
