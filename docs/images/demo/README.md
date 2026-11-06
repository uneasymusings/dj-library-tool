# GitHub walkthroughs

[Back to djlib](../../../README.md) · [Try the interactive demo](https://uneasymusings.github.io/dj-library-tool/preview/)

The current gallery features **Avalon Emerson, Ben UFO, Four Tet, Floating Points and Skrillex**, captured with djlib **0.1.0a22**. Each clip runs the public CLI against an isolated library of original four-second tones, with labels from a selected published tracklist. Library ownership is illustrative. Saved BPM/key are unverified example annotations, not measured metadata for those songs.

The two commands are real: check the text tracklist and read crate prep. CLI stdout fills a plain terminal viewport, replayed with readable pauses. No presentation frame, title cards or chapter bars are added. Rip and Shop are optional next steps. There is no help-screen interlude or live provider search. No fresh song recognition, source-audio download, native rekordbox import, USB export or CDJ playback occurs in these clips. The USB row remains unchecked.

| DJ | Selection source | What this demo shows |
| --- | --- | --- |
| Avalon Emerson | [Artist-linked 9000Streams Studio Mix](https://www.avalonemerson.com/) · [video](https://www.youtube.com/watch?v=zn-Mpr9CPkc) | Four exact matches, one other mix, two missing, one ID |
| Ben UFO | [Official Boiler Room London mix, 21 September 2010](https://music.apple.com/us/album/boiler-room-ben-ufo-in-london-sep-21-2010-dj-mix/1549936423) | Three exact matches, one other mix, two missing, two IDs |
| Four Tet | [Official Lollapalooza 2024 upload](https://www.youtube.com/watch?v=C2f_OA2Ce14) | Four exact matches, two other mixes, two missing |
| Floating Points | [NTS, 10 February 2025](https://www.nts.live/shows/floating-points/episodes/floating-points-10th-february-2025) | Four exact matches, one other mix, three missing |
| Skrillex | [Official QUEST FOR FIRE Basement Set chapters](https://www.youtube.com/watch?v=hb0XLX0b4Y4) | Five exact matches; three unavailable recordings remain out of the crate |

Selections are intentionally short and are not complete set tracklists. Four Tet's selection also uses the earlier recorded identification run documented in the [a20-tagged README](https://github.com/uneasymusings/dj-library-tool/blob/v0.1.0a20/README.md). Avalon uses published description entries preserved in the repository. Apple Music's `[Mixed]` suffixes are omitted. NTS timestamps behind supporter access are neither used nor inferred. NTS's spelling of Minnie Riperton and Source Direct's “Secret Liaisons” is corrected using [Apple Music](https://music.apple.com/us/song/1444020200) and [Source Direct's Bandcamp](https://sourcedirectmusic.bandcamp.com/album/secret-liaisons).

GIFs loop inline in the README. The release's `gray-demo-*.mp4` downloads are higher-resolution silent videos with playback controls. The `*-poster.png` stills provide a static alternative. JetBrains Mono is used under the SIL Open Font License; its license and the [reproduction scripts](../../../scripts/showcase/README.md) are included.

Earlier Caribou and Mura Masa GIFs remain historical a17 recordings and are not part of the current five-DJ gallery. The [a20 tag](https://github.com/uneasymusings/dj-library-tool/tree/v0.1.0a20/docs/images/demo) preserves the prior gallery and its captions.

The browser walkthrough uses actual screenshots of the public GitHub Pages preview: the Four Tet set, optional Rip panel and crate-prep view. It is a paced sequence of captured states, not continuous device automation footage. The production frontend is unchanged apart from deferring startup for the read-only demo transport. All writes are blocked. Desktop and 390px phone-width checks covered panel visibility, Escape dismissal, optional store links and local-app instructions.

## Watch the walkthroughs

Follow **Avalon Emerson, Ben UFO, Four Tet, Floating Points, and Skrillex** through the actual terminal: **check the set → inspect the crate**. Real CLI output, edited for pacing, with a generated demo library and illustrative saved BPM/key.

<p align="center">
  <a href="https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a22/gray-demo-avalon-emerson.mp4"><img src="avalon-emerson.gif" width="100%" alt="Avalon Emerson walkthrough: check an eight-track selection, distinguish owned records and another mix, find missing tracks, then inspect crate prep."></a>
</p>

**Avalon Emerson / 9000Streams.** Exact mixes, missing records, and a crate with a clear next step. [Watch MP4](https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a22/gray-demo-avalon-emerson.mp4) · [Still image](avalon-emerson-poster.png) · [Set source](https://www.youtube.com/watch?v=zn-Mpr9CPkc) · [Open this set in the demo](https://uneasymusings.github.io/dj-library-tool/preview/#/sets/request_db35a700d0a1417993a2a99689ff919c)

<details>
<summary><b>Ben UFO</b> — an ID stays an ID; another mix stays out</summary>
<br>
<img src="ben-ufo.gif" width="100%" alt="Ben UFO walkthrough: two unknown IDs remain unnamed, the Cassy remix stays distinct from the original, and only exact owned tracks enter the crate.">

Selected tracks from **Boiler Room London, 21 September 2010**. The demo leaves the two IDs unnamed and keeps the wrong mix out of the crate. [Watch MP4](https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a22/gray-demo-ben-ufo.mp4) · [Still image](ben-ufo-poster.png) · [Published tracklist](https://music.apple.com/us/album/boiler-room-ben-ufo-in-london-sep-21-2010-dj-mix/1549936423) · [Open this set in the demo](https://uneasymusings.github.io/dj-library-tool/preview/#/sets/request_a7020bcbf173494cae3a8ba42824a486)
</details>
<details>
<summary><b>Four Tet</b> — a festival tracklist becomes a practical crate</summary>
<br>
<img src="four-tet.gif" width="100%" alt="Four Tet walkthrough: selected Lollapalooza tracks checked against a demo library, with two alternate mixes excluded and a prep view for the exact matches.">

Selected tracks from **Lollapalooza 2024**. The original recordings of “Goblin” and “In Your Head” do not substitute for the requested edits. [Watch MP4](https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a22/gray-demo-four-tet.mp4) · [Still image](four-tet-poster.png) · [Set source](https://www.youtube.com/watch?v=C2f_OA2Ce14) · [Open this set in the demo](https://uneasymusings.github.io/dj-library-tool/preview/#/sets/request_724ec0dd443844eda5e00cf12a981714)
</details>
<details>
<summary><b>Floating Points</b> — radio discoveries, missing records, saved BPM/key</summary>
<br>
<img src="floating-points.gif" width="100%" alt="Floating Points walkthrough: selected NTS tracks become an ownership check and a crate with explicit saved-analysis gaps.">

Selected tracks from **NTS, 10 February 2025**, supplied as a copied text file. The clip shows ownership and saved-analysis gaps; it does not imply direct NTS-link import. [Watch MP4](https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a22/gray-demo-floating-points.mp4) · [Still image](floating-points-poster.png) · [Published tracklist](https://www.nts.live/shows/floating-points/episodes/floating-points-10th-february-2025) · [Open this set in the demo](https://uneasymusings.github.io/dj-library-tool/preview/#/sets/request_9c6f4b54af5948dfbd61a2f19946d9e2)
</details>
<details>
<summary><b>Skrillex</b> — prep the exact recordings you own</summary>
<br>
<img src="skrillex.gif" width="100%" alt="Skrillex walkthrough: a selection from the official Quest For Fire Basement Set, with five exact demo-library matches and the unavailable VIP excluded.">

Selected chapters from **QUEST FOR FIRE Basement Set**. Five exact matches enter this demo crate; the unavailable VIP is not replaced with another recording. [Watch MP4](https://github.com/uneasymusings/dj-library-tool/releases/download/v0.1.0a22/gray-demo-skrillex.mp4) · [Still image](skrillex-poster.png) · [Official set and chapters](https://www.youtube.com/watch?v=hb0XLX0b4Y4) · [Open this set in the demo](https://uneasymusings.github.io/dj-library-tool/preview/#/sets/request_c5e238332f1b4d8dbec7b6a206184e48)
</details>
