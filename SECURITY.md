# Security and privacy

djlib runs on your computer, for you. There is no djlib account, no djlib server and no telemetry. It reaches other services only for the work you ask for, and this page says which ones and what they get.

## What stays on your computer

- **Your music.** djlib indexes the folders you give it, in place. It never moves, renames or retags your files.
- **djlib's folder** (`djlib doctor` shows where): the index of your music (file paths, tags, hashes), your sets, crates, shopping lists, downloads and logs. Logs can hold file paths. `runtime/downloads.log` leaves out links, cookies and your home folder.
- **A set's audio**, downloaded only so djlib can listen to it. It is deleted when the listen ends; audio left by a paused or failed listen goes after a day.

## What leaves your computer, and to whom

| Who | When | What they get |
| --- | --- | --- |
| **Shazam**, through the unofficial shazamio library, with no account | djlib listens to a set | A fingerprint (signature) made on your computer from each 12-second listening window. The audio itself isn't sent. |
| **AudD or ACRCloud** | Only if you set one up with your own account | The set's audio, in 12-second windows (AudD can take the whole file). |
| **YouTube and SoundCloud**, through yt-dlp | You check a set link, or `--fetch` looks for missing songs | The set's link (its page, description and comments are read), searches by artist and title, and the downloads. Bandcamp links can be downloaded too. |
| **MixesDB** | A set's page has no tracklist | Lookups of that set, at most 4 requests, 4 seconds apart, with a User-Agent that names djlib. `DJLIB_MIXESDB=off` turns this off. |
| **GitHub** | `djlib upgrade` and the installer | A request for the newest release, then its download; the Python libraries it needs come from PyPI. The installer also gets uv from astral.sh and FFmpeg through Homebrew. |
| **Your assistant** (Claude Code, Codex) | You use djlib's MCP tools | What the tools return: tracklists, song names and file paths from your index. That app's own data handling applies. |

The Beatport, Bandcamp and Traxsource links on a shopping list are searches you open yourself; djlib doesn't contact those stores, and never buys anything. 1001Tracklists only shows tracklists to people in a browser, so djlib never fetches it.

## What djlib writes

- **Never** your music files, rekordbox's database or the USB stick. rekordbox does its own import and export through its own menus; djlib reads the stick back afterwards to check it.
- **Tags only on copies djlib makes.** A song `--fetch` downloads is saved as an MP3 in djlib's folder, tagged with the artist, title and mix you asked for and a comment that reads “Web rip from … (stream quality) — buy before playing out”. The file as it arrived stays untouched in `incoming/`. Working copies prepared for a DJ app get tags too; the originals they come from don't.

## What macOS lets it do

- **Accessibility**, for the app you run djlib in (Terminal, iTerm, VS Code or Claude): to use rekordbox's menus for the playlist and the USB export. djlib presses keys only once rekordbox is in front and the expected dialog has focus.
- **Full Disk Access**, only if macOS keeps your Shazam history private, and to read an iPhone backup.
- **Automation of Music**, only for `djlib set shazam:apple-music`.
- `--when-idle` reads how long the keyboard and mouse have been idle, which needs no permission.

## Text from the web

Set descriptions, listener comments and MixesDB pages are written by strangers. djlib reads them as tracklist data, never as commands, and its MCP server tells assistants to treat that text as untrusted.

## The background service

djlib's service listens only on `127.0.0.1`, needs a private token for every request, and refuses other hosts and browser origins; clients check the address before they use it. The review page carries the token after `#` in its link, so it stays in your browser. On macOS and Linux djlib's folder and token are readable only by you; on Windows, folder permissions haven't been checked yet. Any program running as your user can read djlib's folder and use the service. It is not built for several users or for a network: don't expose it.

Song downloads take only public HTTPS links on YouTube, SoundCloud and Bandcamp, up to 30 minutes each, with fixed file names and limits on time and size. yt-dlp runs in its own process and ignores your yt-dlp settings, browser cookies and plugins; djlib doesn't download DRM-protected recordings. Redirects and CDNs are left to yt-dlp, so this is not a hardened network sandbox. File paths are resolved, symlinks included, and must lie inside the music folders you gave djlib.

## Reporting a vulnerability

Report it privately: [open a security advisory](https://github.com/uneasymusings/dj-library-tool/security/advisories/new) (GitHub private vulnerability reporting). Please don't put exploit details, your music index, tokens or unredacted logs in a public issue.

djlib is alpha software and has had no security audit. Known gaps are in [architecture](docs/ARCHITECTURE.md) and [status](docs/STATUS.md).
