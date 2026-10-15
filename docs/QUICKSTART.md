# Quick start

From a set link or your Shazams to a USB stick, on one page. Every command here is safe to run again.

## 1. Install

Paste this to Claude Code or Codex: “Set up djlib for me by following <https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/SETUP.md>”. Or in Terminal (Mac or Linux):

```bash
curl -LsSf https://raw.githubusercontent.com/uneasymusings/dj-library-tool/main/install.sh | sh
```

It asks where your music is (drag the folder in) and indexes it. Nothing is moved, renamed or retagged. Skipped that? Run `djlib init ~/Music && djlib scan`. Music on another drive? `djlib init '/Volumes/DJ Drive/Music'` adds it; then `djlib scan`. Windows: [Install](INSTALL.md).

## 2. Every track in a set

```bash
djlib set 'https://www.youtube.com/watch?v=…' --no-rekordbox
```

djlib reads the description, chapters, MixesDB and the comments. When the tracklist is missing, has IDs or names too few songs for the set's length, it says so and listens to the set with Shazam. A line like “Artist – live from Boiler Room …” is about the set, not a song. An hour-long set usually takes 5–10 minutes; when Shazam is busy it can take half an hour. Ctrl-C stops it; run the same command later to carry on. A set djlib already heard comes back at once. You get the whole set in order: time, track, how sure, and what you own. Where your terminal shows links, each time opens the set at that moment.

| Sure | What it means |
| --- | --- |
| ✓ confirmed | Shazam heard it where a tracklist puts it, or heard it 3 or more times. |
| ◐ likely | A tracklist names it but Shazam didn't hear it, or Shazam heard it twice. |
| ! weak | Only a listener's comment names it, or Shazam heard it just once. |
| ✗ conflict | The tracklist names one song; Shazam heard another there. |
| ○ ID | Nobody knows yet. Shazam's guess shows as “ID — maybe …”, never as its name. |

Likely and weak lines are worth a listen before you play them. Want just the tracklist, to post or share? `djlib identify 'LINK'`.

**Have the 1001Tracklists list?** djlib never fetches 1001Tracklists, so copy it yourself (or let an assistant that can browse do it). Type `djlib set 'LINK' --tracklist paste` with the set's own YouTube or SoundCloud link, then copy the tracklist on the page and press Enter. djlib merges it with what Shazam hears, so it listens to the set first (5–10 minutes an hour; at once if it heard the set before). Your list is kept for that set; another `--tracklist` replaces it. No time to listen? Copy the list and run `djlib set paste`: at once, with your list's own times but no links into the set.

**Just a tracklist?** Copy it and run `djlib set paste`. One “Artist - Title (Mix)” per line; numbers, times, `[Label]` and IDs are fine, and a heading line names the set. A spreadsheet with Artist and Title columns works too: copy the cells with the header row, or run `djlib set 'Warehouse Friday.csv'`.

## 3. Your Shazams (Mac)

Run `djlib shazam status`. It finds the songs you Shazamed on this Mac, and your iPhone's once Shazam syncs with iCloud, and gives you the command to check them: your last 30 days first, when you have more than 100. If it finds nothing, it prints three steps to turn on the sync: do them, Shazam one song, and check again in a few minutes.

- `djlib set shazam:2026-10-10`: what you Shazamed since that day.
- `djlib set shazam:new`: what's new since your last Shazam set. The first time, that's everything.
- `djlib set shazam`: your whole history, oldest first, 1,000 songs at a time. `djlib shazam status` then offers the rest.
- `djlib set ~/Downloads/shazamlibrary.csv`: a CSV from shazam.com/myshazam (Download CSV, if your account shows it).
- No iPhone sync? `djlib set shazam:apple-music` reads Apple Music's “My Shazam Tracks” playlist, if Shazam saves your songs there. Music opens, and macOS asks once whether djlib may use it.

## 4. What do I own?

- **✓ owned**: you have this exact mix. It goes in the crate, in set order. “Owned under another spelling” means your file is tagged the way a tracklist (MixesDB, or the one you pasted) spells the artist (Robert Glasper, not Robert Glasper Experiment). Same mix.
- **? maybe yours**: you have a file with the same title and mix under another artist name (Robert Glasper for Robert Glasper Experiment). Common with Shazams, which have no tracklist to spell it. It stays off your shopping list; if it's the song, pick Use this on the review page and it counts as owned.
- **✗ missing**: you don't have it. It goes on your shopping list.
- **≈ Radio Edit**: you own another mix. djlib only counts the exact mix, so it stays out of the crate; drag yours into the playlist if it's the one you play. Shazam often names the streaming version, so this is common with Shazams.
- **! owned, pick a copy**: you have this mix more than once. Pick one on the review page with Use this.
- **! owned, file unavailable**: the drive with that file isn't plugged in.

`djlib ui` opens the review page: your sets, crates and library in the browser. A set's page has its times (each opens the set at that moment), how sure djlib is, store searches, a “Copy N to buy” button, and its crate with the commands for rekordbox and your USB. A long Shazam list opens on the songs you don't own yet, newest first.

## 5. rekordbox and your USB (Mac)

`djlib set 'LINK'` also makes a rekordbox playlist; `djlib set 'LINK' --usb` puts it on your USB stick too. The first time, allow the app you run djlib in (Terminal, iTerm, VS Code or Claude) under System Settings → Privacy & Security → Accessibility.

djlib makes the playlist through rekordbox's own menus, so tracks rekordbox already knows keep their analysis and cues. For the USB, click the playlist in rekordbox once when asked. rekordbox exports it; djlib reads the stick back and checks every track, in order, byte for byte. Test the stick on a player before a gig.

Checked a set already? `djlib rekordbox push last` or `djlib rekordbox usb last` (`last` is the set you checked most recently, and its crate). `djlib rekordbox push last --when-idle 60` waits until you step away from the computer.

## 6. Missing songs

`djlib requests report last` is your shopping list: each missing song with Beatport, Bandcamp and Traxsource searches, saved as “Set name - to buy.txt” in the “Shopping lists” folder of djlib's folder. Check it's the mix you want before you buy. After buying, run `djlib scan`, then `djlib requests refresh last`. `djlib set 'LINK' --fetch` downloads clear matches from YouTube and SoundCloud as MP3 after you confirm, official uploads first. That's stream-quality audio, marked “buy before playing out” in rekordbox's Comments: buy the real file for the club.

## Limits

- Shazam is used unofficially and can slow down or stop working. AudD and ACRCloud are licensed options with your own key: [song recognition](INSTALL.md#song-recognition).
- Songs Shazam doesn't know, short blends and heavy edits stay IDs.
- Set links: YouTube and SoundCloud only. Mixcloud doesn't allow downloads; use the same set on YouTube or SoundCloud, or its copied tracklist.
- `paste` reads the clipboard on a Mac. On Linux and Windows, save the tracklist as a text file, or pipe it in with `-`.
- Driving rekordbox needs a Mac. On Linux and Windows djlib builds the crate, and you import it yourself.
- djlib checks the stick, not the player. CDJ and XDJ playback isn't verified.
- Up to 100,000 files per scan.

## What djlib won't do

- Scrape or copy 1001Tracklists, or get round its captcha.
- Rotate addresses or listen in parallel to get past Shazam's limits.
- Buy music or fill shopping carts. It gives you search links.
- Post tracklists anywhere for you.
- Edit rekordbox's database or write to your USB stick. rekordbox does the export.
- Move, rename or retag your music.
- Download anything unless you ask with `--fetch`.

More: [Troubleshooting](TROUBLESHOOTING.md) · [Advanced commands](ADVANCED.md)
