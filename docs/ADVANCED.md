# Advanced commands

`djlib --help` lists the commands most DJs need. These commands still work but are hidden from it. Run any of them with `--help` for its options.

Commands that start a background job take `--key TOKEN` in scripts: a retry token you choose, so retrying never runs the job twice. A terminal makes one up for you.

## Library and crates

- `djlib collections`: the old name of `djlib crates`; lists saved crates.
- `djlib collection CRATE`: the old name of `djlib crate`; shows a crate's tracks.
- `djlib reconcile --file FILE`: update the catalog after files moved or changed on disk; the JSON file lists each change.
- `djlib organize`: add DJ notes (tags, energy, set role, BPM/key) to tracks and build ordered, filtered collections from JSON files.
- `djlib plan --file FILE`: plan a collection from a JSON tracklist of local files.
- `djlib start PLAN_ID`: build a planned collection.
- `djlib download --file FILE`: download selected public YouTube, SoundCloud or Bandcamp recordings (`djlib set --fetch` does this for a set).
- `djlib source-inspect URL`: read a set's published description and chapters.

## rekordbox, Serato and USB by hand

- `djlib export CRATE`: write a hash-checked manifest, an M3U playlist and experimental rekordbox XML.
- `djlib import-rekordbox FILE`: bring BPM and key from a rekordbox collection XML into your catalog (`djlib rekordbox sync` reads them automatically).
- `djlib usb-preflight PATH`: check a mounted drive's free space without writing to it.
- `djlib delivery`: the step-by-step delivery workflow for rekordbox/Serato imports and USB sticks, with recorded observations (`plan`, `prepare`, `bind-device`, `observe`, `verify-app`, `verify-device`, `inspect-native-xml`).

## Setup and scripting

- `djlib demo`: try the whole workflow on three generated tones, in a new workspace.
- `djlib schemas`: print the JSON input schemas.
- `djlib capabilities`: list implemented and planned capabilities.
- `djlib version`: print the version as JSON (`djlib --version` prints it as text).
- `djlib completion [SHELL] [--install]`: print or install shell completion for bash, zsh, fish or PowerShell.

## Handles instead of IDs

`djlib crate`, `djlib export`, `djlib delivery plan --collection` and `djlib requests get/collect/refresh/report/resolve` accept a name (any case), `last` for the newest, or the first 6 or more characters of an ID instead of the full ID. `requests refresh`, `report` and `resolve` use the list's current revision unless you pass `--revision`.

`--json` and `-w/--workspace PATH` work before or after the command name.
