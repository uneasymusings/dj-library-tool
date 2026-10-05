# Deliver a collection through a native DJ app

This workflow describes **0.1.0a4**. It prepares isolated working copies and records the path to a native app, with a separate route for device delivery. Earlier a3 requires a matching pilot before full preparation and performs analysis/app checks synchronously. See [status](STATUS.md) for actual validation and publication evidence. It does not operate rekordbox or Serato, write their databases, format a USB, or copy anything onto that USB. Native import, analysis, export, and playback remain actions in the selected DJ app and on the intended hardware.

Use `delivery list --query NAME` to rediscover saved work, then `delivery get DELIVERY_ID` for its next blocker. App-only work progresses through **cataloged → prepared for import → imported → analyzed → working files checked**. A separate USB delivery continues through **native exported → device library checked → hardware playback checked**, with device byte readback. A download, M3U, XML, database filename, or successful hash readback alone cannot establish native app or hardware behavior.

## Choose the destination before preparing music

| Workflow | Destination | Required target information |
| --- | --- | --- |
| `rekordbox_import` | Local rekordbox Collection/playlists and analysis | Accepted collection IDs and installed app version. No USB or player profile. |
| `serato_import` | Local Serato regular crates and analysis | Accepted collection IDs and installed app version. No USB or controller model. |
| `rekordbox_usb` | USB exported by rekordbox for a standalone player | App version and exact supported hardware profile for preparation; USB mount and actual firmware/playback evidence for device completion. |
| `serato_portable` | Regular crates and audio copied through Serato for a Serato computer/setup | App version for preparation; actual USB mount and destination computer/controller/OS checks for device completion. No standalone-player profile is implied. |

```bash
djlib --workspace /path/to/dj-workspace delivery targets
```

The small profile table covers `cdj-2000nxs`, `cdj-3000`, `xdj-rx3`, and `opus-quad`. It includes source links and known audio/filesystem restrictions. Unknown hardware is blocked rather than guessed. CDJ-2000NXS does not support FLAC; RX3 and CDJ-3000 have different lossless sample-rate limits. These are technical checks, not firmware or hardware certifications. A null partition-scheme allowlist means the sources do not establish an exhaustive list; it does not mean every partition scheme works.

OneLibrary is the current name for Device Library Plus. OPUS-QUAD requires that library; the other three profiles use Device Library. CDJ-3000 firmware 3.30 was withdrawn, and AlphaTheta's January 2026 notice says the model reverted to Device Library. Confirm the installed firmware, especially on a unit still using 3.30. [AlphaTheta OneLibrary](https://alphatheta.com/en/onelibrary/), [CDJ-3000 notice](https://www.pioneerdj.com/en/news/2026/cdj-3000-firmware-ver330-important-notice/).

Keep existing native libraries and USB contents intact. If the current filesystem or device is unsuitable, record the blocker and choose a separately approved remedy. This workflow never reformats a device or overwrites an existing native library by itself.

## Start with the app when the player is not known

Choose `rekordbox_import` or `serato_import` when the immediate task is organizing music in that application. An unknown player/controller is not a blocker for these workflows. Create `app-trial.json` with actual collection IDs and the installed version:

```json
{
  "name": "Local app pilot",
  "collection_ids": ["COLLECTION_ID"],
  "workflow": "rekordbox_import",
  "app_version": "7.2.8",
  "audio_mode": "preserve",
  "phase": "pilot",
  "pilot_size": 3
}
```

For Serato, replace the workflow with `serato_import` and supply its actual app version. Omit `hardware_profile`; it applies only to `rekordbox_usb`.

```bash
djlib --workspace /path/to/dj-workspace delivery plan --file app-trial.json
djlib --workspace /path/to/dj-workspace delivery prepare DELIVERY_ID \
  --revision CURRENT_REVISION --key local-app-pilot-v1
djlib --workspace /path/to/dj-workspace jobs wait JOB_ID --timeout 30
djlib --workspace /path/to/dj-workspace jobs items JOB_ID
```

After all preparation items succeed, import the provided M3U8 in rekordbox, or import the working files into regular Serato crates matching the manifest. Inspect all memberships/paths and analyze only the selected new working copies. Record `imported` and `analyzed` using the observation contract below, then refresh the working-file evidence:

```bash
djlib --workspace /path/to/dj-workspace delivery verify-app DELIVERY_ID \
  --revision CURRENT_REVISION
djlib --workspace /path/to/dj-workspace jobs wait VERIFICATION_JOB_ID --timeout 30
djlib --workspace /path/to/dj-workspace jobs items VERIFICATION_JOB_ID
```

A passed `analyzed` observation and `verify-app` each return a durable job. Wait for the analysis job before submitting app verification with the updated revision. A successful completed job returns only `delivery_id`, `revision` and `evidence_committed: true`; it never returns `ready_for_app_use`. Read `delivery get` for `app_requirements_met_at_last_check`, blockers and `evidence.app_readback.checked_at` (also `last_app_readback_at`). These describe saved file checks and operator-reported native behavior, not a new check. `delivery get` reports saved evidence without rehashing; submit verification against its current revision for a new check. The engine does not read the app's database or independently prove musical accuracy.

App-first preparation checks native-app input profiles rather than a standalone player's limits. Rekordbox numeric limits come from its manual; the Serato profile uses documented format families with an explicitly conservative engine subset because the cited DJ Pro pages do not establish exhaustive numeric maxima. Out-of-subset files require review or an explicitly chosen conversion; preserve remains the default. Native app trials remain necessary.

No device binding, USB readback or hardware-playback observation is needed to complete this app-only scope. To prepare a USB afterward, create a separate `rekordbox_usb` or `serato_portable` request and complete that workflow. An app-only pilot cannot be cited as a validated USB pilot, but an unvalidated local full preparation may proceed without any pilot ID. [rekordbox import/analysis manual](https://cdn.rekordbox.com/files/20260807093645/rekordbox7.2.18_manual_EN.pdf), [Serato import](https://support.serato.com/hc/en-us/articles/223446528-Adding-files-to-the-Serato-DJ-Pro-Library).

## Inspect a native rekordbox snapshot

After import, use rekordbox's supported **File → Export Collection in xml format** operation. Save a new XML file within the library workspace or an explicitly allowed root, preserving any existing snapshot. Then compare it with the prepared delivery:

```bash
djlib --workspace /path/to/dj-workspace delivery inspect-native-xml DELIVERY_ID \
  /path/to/dj-workspace/native-collection.xml --revision CURRENT_REVISION
```

The MCP equivalent is `djlib_inspect_delivery_native_xml(delivery_id, revision, path)`. It checks exact prepared working paths, native playlist membership/order and the XML's declared product version against the request. It reports missing, duplicate, ambiguous and mismatched entries, with unknown BPM/key preserved. The input must be a native rekordbox export; the engine's experimental XML handoff is not accepted as native evidence. The format follows [AlphaTheta's official XML specification](https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf).

This bounded, read-only comparison opens no audio paths referenced by the XML, changes no delivery state and never returns app/device readiness. The snapshot may become stale, and its product label is not independently authenticated. A match supports snapshot membership evidence only; inspect loading and musical BPM/key/grid accuracy in the app and use the normal observations plus fresh verification for readiness. A declared version mismatch remains unresolved rather than being rewritten to match.

The report's known BPM/key values can support explicit catalog annotations for the corresponding recording/revision. Use `source: "operator"`, note the XML checksum/version and keep `verified: false` until actual musical review. Unknown or ambiguous values remain unset. `native_tag` is reserved for matching freshly read catalog-file tags, not values from this XML. After annotation, create a new filtered collection and delivery snapshot when needed.

## USB example 1: a three-track rekordbox trial

Start with accepted, inspected collection members. Substitute real collection IDs, the installed app version, and the actual player profile in `delivery.json`:

```json
{
  "name": "Friday warm-up pilot",
  "collection_ids": ["COLLECTION_ID"],
  "workflow": "rekordbox_usb",
  "app_version": "7.2.8",
  "hardware_profile": "cdj-3000",
  "audio_mode": "preserve",
  "phase": "pilot",
  "pilot_size": 3
}
```

The version and hardware here are examples, not defaults or recommended upgrades. A pilot selects up to five recordings, sampling collections in round-robin order; three is the default. Inspect the selected recordings and retained playlist memberships before proceeding.

```bash
djlib --workspace /path/to/dj-workspace delivery plan --file delivery.json
djlib --workspace /path/to/dj-workspace delivery get DELIVERY_ID
djlib --workspace /path/to/dj-workspace delivery prepare DELIVERY_ID \
  --revision CURRENT_REVISION --key friday-pilot-prepare-v1
djlib --workspace /path/to/dj-workspace jobs wait JOB_ID --timeout 30
djlib --workspace /path/to/dj-workspace jobs items JOB_ID
djlib --workspace /path/to/dj-workspace delivery get DELIVERY_ID
```

Use the IDs and current revision from JSON responses. Preparation is a durable job; retain its stable key and inspect item outcomes. Every frozen recording must prepare successfully before native-stage observations can pass. A job completed with gaps is not a complete handoff.

Preparation creates a separate export directory containing labeled working audio, one M3U8 per nonempty selected collection, `delivery-manifest.json`, and `NATIVE_STEPS.txt`. It checks source hashes, decodes the prepared audio, and checks the selected player profile. Keep those working paths available to the native app.

Player assessment conservatively covers mono/stereo audio with known channel metadata. Multichannel audio is outside that assessed subset. A supplied output extension must also match the codec's supported suffixes; a misleading filename cannot stand in for format compatibility. These checks do not establish complete container-header compatibility or hardware playback.

| Audio mode | Behavior |
| --- | --- |
| `preserve` | Default. Copy the source format into separate app working storage; apply selected display labels there. Unsupported target formats block preparation. |
| `mp3_320` | Explicit compatibility option. Encode when needed to stereo 44.1 kHz/320 kbps MP3. An existing MP3 at 44.1/48 kHz with at most two channels is reused rather than recompressed; its existing bitrate remains unchanged. |
| `wav16_44100` | Explicit compatibility option. Create stereo, 16-bit, 44.1 kHz WAV working copies. |

No mode improves source fidelity. Supplied artist/title/version tags are display labels, not proof of exact recording identity. Conversion never silently replaces the catalog originals.

Plans also freeze explicit catalog annotations and their revision. Preparation writes supplied BPM/key/genre tags plus notes, tags, role and energy comments into the separate working copies (ID3 for WAV/AIFF/MP3, FLAC tags or MP4 atoms). Existing tags remain where no override is supplied, and the manifest preserves exact annotations and provenance. These writes are display metadata, not native analysis or evidence of musical accuracy. MP4's integer tempo field cannot represent fractional BPM: the exact value stays in the manifest/freeform tag with a warning, so check its native display. Later annotation edits require a new delivery snapshot.

Read the generated manifest, then import its M3U8 playlists in **rekordbox EXPORT mode → File → Import → Import Playlist**. Check membership; analyze the new working copies for BPM/Grid and Key, then audition and correct beatgrids/cues as needed. Preserve existing manual edits rather than reanalyzing the whole library. The native application owns this work. [rekordbox manual, playlist import and analysis](https://cdn.rekordbox.com/files/20260807093645/rekordbox7.2.18_manual_EN.pdf).

Record `imported`, then `analyzed`, using actual observations as described below. Bind the mounted USB root; an ordinary directory cannot stand in for a device:

```bash
djlib --workspace /path/to/dj-workspace delivery bind-device DELIVERY_ID \
  /Volumes/DJ_USB --revision CURRENT_REVISION
```

Export the selected playlists through rekordbox's **Devices** workflow or Sync Manager, using the target's required library format. Wait for completion, then browse the device library in rekordbox and check playlist membership/counts and track loading. Record `native_exported`, wait for its check job to succeed, then read the updated revision before recording `device_library_checked`. Preserve an existing device library before any separately approved library conversion. AlphaTheta documents native USB export and library selection in its [USB export guide](https://cdn.rekordbox.com/files/20260318114024/OneLibrary-Compatible-USB-Device-Export_en.pdf).

```bash
djlib --workspace /path/to/dj-workspace delivery verify-device DELIVERY_ID \
  --revision CURRENT_REVISION
```

After successful readback, safely eject through the app and load/play every pilot recording on the specified player. Check audibility, playlist browsing, waveform/grid and intended cue behavior. Record `hardware_playback` only after that test. Reconnect the same volume and run `verify-device` again before departure for fresh byte/volume evidence, then eject safely. The hardware stage is an operator report, not an automated hardware test.

## Record evidence accurately

Create an observation file only after the corresponding action. This example describes a **hypothetical** import of three recordings in one collection; replace all counts, IDs, version, revision, observer and notes with actual results:

```json
{
  "revision": 1,
  "stage": "imported",
  "app_version": "7.2.8",
  "track_count": 3,
  "playlist_counts": {"COLLECTION_ID": 3},
  "checked_recording_ids": ["RECORDING_ID_1", "RECORDING_ID_2", "RECORDING_ID_3"],
  "observer": "Operator name",
  "notes": "Inspected all three imported working copies and the selected playlist in the app.",
  "method": "native_app_ui",
  "outcome": "passed"
}
```

```bash
djlib --workspace /path/to/dj-workspace delivery observe DELIVERY_ID --file observation.json
djlib --workspace /path/to/dj-workspace delivery get DELIVERY_ID
```

| Stage | Required observation |
| --- | --- |
| `imported` | Intended working copies and exact playlist/crate membership are visible in the app. |
| `analyzed` | The selected tracks were analyzed and their relevant grids/keys/cues inspected. |
| `native_exported` | The native app completed export/copy to the bound device. |
| `device_library_checked` | Browse the device in the native app; confirm membership, counts and loading. |
| `hardware_playback` | Actual playback on the target player or destination Serato setup; use `method: "physical_hardware"`. |

The first four stages use `method: "native_app_ui"`. Counts and checked recording IDs must match the frozen manifest for passing observations. Pilot hardware playback covers every pilot recording; full-delivery hardware playback permits an explicit sample. A full sample must never be described as playback verification of every recording. Use `outcome: "failed"` and concrete notes for a failed attempt.

A passing `hardware_playback` observation for `rekordbox_usb` must also include:

- `hardware_profile`: the actual player profile, matching the delivery request.
- `firmware_version`: the version read from that player; do not substitute an assumed or recommended version.
- `storage_recognized: true`: observed recognition of the intended USB on that player.

CDJ-3000 firmware `3.30` is rejected because it was withdrawn. Prefix/case variants such as `v3.30` and `V3.30` are rejected too. These required fields record operator evidence; they do not mean the engine interrogated the player.

Observations are stored as `operator_reported`, with timestamps and revision checks. They are claims supplied by the operator, not independently authenticated proof of app behavior. Earlier replacement observations invalidate later stages; rebinding a device invalidates device-dependent evidence. Read the new revision after each change.

Analysis may change tags on working copies. When recording successful analysis, an unchanged file hash takes the fast path without redundant decoding. Changed file bytes trigger fresh stream-property and decoded-audio checks against the prepared copy, then the engine captures the new file hashes for device readback. Changed audio is blocked. This reconciliation is limited to these isolated copies; it does not adopt arbitrary changes to original catalog files or parse native analysis databases.

Successful `analyzed`/`native_exported` observations and `verify-app` queue `delivery_check` jobs with per-track progress. Save the job ID, poll or use `jobs wait`, inspect failures, and read the completed result before continuing. These submissions derive an idempotency key from delivery ID, revision, operation and complete observation. Retrying the identical intent returns the same job even after its successful evidence commit advances the revision. A new fresh check uses the delivery's latest revision. Pause/cancel/generation checks fence the final evidence commit; a paused job cannot newly assert readiness. Native-export jobs check every working copy against its exact analyzed hash and validate the bound volume at acceptance and finalization. They verify the supplied observation, not perform native export. Other observation stages and device verification remain direct revision-checked operations. Metadata reads and request-ledger refreshes also retain their own bounded synchronous behavior.

## USB example 2: a portable Serato library

Create a second request for the installed Serato version:

```json
{
  "name": "Friday Serato pilot",
  "collection_ids": ["COLLECTION_ID"],
  "workflow": "serato_portable",
  "app_version": "3.1.5",
  "audio_mode": "preserve",
  "phase": "pilot",
  "pilot_size": 3
}
```

Use the same `delivery plan`, `prepare`, `get`, `bind-device`, `observe`, and `verify-device` commands with this delivery's IDs/revisions. This route does not apply a rekordbox hardware profile. Confirm destination OS, writable volume, and app format support separately.

Import the working audio through Serato's Files panel and create **regular crates** matching the manifest. The M3U8 files describe membership; the engine does not create native crates. Analyze the selected tracks and inspect them. Serato Pro 3.3.5 and earlier require disconnected DJ hardware for analysis; newer versions have different behavior. Follow the installed version's instructions. [Serato analysis](https://support.serato.com/hc/en-us/articles/14361068095759-Analyzing-Files).

In Serato's Files panel, drag each regular crate to the bound USB and choose **Copy**, then wait for completion. Serato's documented portable-library procedure transfers crates and audio; Smart Crates cannot be transferred this way. Reconnect and inspect the portable crates, preferably on the destination computer, then test playback with the intended setup and record the same evidence stages. This prepares a Serato library for a computer, not a standalone CDJ library. [Serato portable library](https://support.serato.com/hc/en-us/articles/202304844-Using-a-USB-external-hard-drive-for-your-portable-library), [Files-panel copy](https://support.serato.com/hc/en-us/articles/12859556039695-Copying-and-Managing-Files-from-the-Files-Tab-Serato-DJ-Pro-Lite).

## Prepare a full collection before the hardware is available

Create a new request with `phase: "full"`. Omit `pilot_delivery_id` when you need local preparation before a physical/native trial. Status and the manifest label this as local files only, with `pilot_validation: "not_supplied"`; completed full preparation reports `prepared_without_validated_pilot: true`. It grants no app or USB readiness. A small trial remains useful before spending time on a large native import.

If you supply `pilot_delivery_id`, it must identify a completed trial matching workflow, app version, hardware profile and audio mode; an incomplete, nonexistent or mismatched claim still returns `PILOT_REQUIRED`. The new full plan freezes current collection membership and annotations. Pilot and full preparations have separate working paths: native cues, grids and analysis on pilot copies are not automatically reused or migrated. Plan the full import accordingly rather than claiming preservation that has not occurred.

Complete every stage required by the full delivery's workflow. Keep the acquisition/missing-track report separate: a finished delivery only covers its frozen accepted recordings, not unavailable or unresolved requested songs.

## What device verification proves

`verify-device` is bounded, read-only SHA-256 presence checking against post-analysis working copies, with volume identity checks. It skips symlinks, Windows reparse points and nested volumes. Incomplete scanning, missing files, changed identity or unsupported safe traversal remain visible blockers. macOS uses `diskutil`; Windows can query the mount manager's volume GUID/filesystem and a bounded metadata-only partition-style IOCTL. Failed partition queries retain any known GUID/filesystem and leave partition style unknown; they do not guess MBR/GPT. Unsupported platforms or failed identity queries cannot satisfy a strong-identity gate. CI fixtures and native system-volume probes do not establish physical FAT32/exFAT USB or player compatibility.

Native database paths are **existence markers only**. The engine does not parse their playlist references, grids, cues, integrity, or freshness. Hash presence cannot prove that the native library references those bytes. Operator inspection and physical playback remain necessary.

`delivery get` reports stored evidence and current blockers but does not rehash audio. `requirements_met_at_last_check` reflects the recorded evidence; `ready_for_departure` additionally requires a successful fresh `verify-device` call. Neither field guarantees future playback or independently verifies operator claims. After later app/device edits, record affected stages again and repeat readback.

## Organize for the set you will play

Use a small practical collection structure, for example **Arrival**, **Warm Groove**, **Lift**, **Peak**, **Closers**, and **Needs Review**. These are suggestions, not automatic genre or energy judgments. A recording can belong to multiple collections without duplicating the catalog audio.

Keep native BPM/key columns sortable after analysis and audition the results. Use separate subjective tags for mood, energy, vocals and role; BPM alone does not establish energy. Add useful comments such as an intro length, vocal entrance, difficult transition, version uncertainty, or source-quality concern. Set cues at musically useful points after listening. Artist/source acquisition batches can remain separate from performance playlists. This follows the practical playlisting, tagging and listening habits described in [Pioneer DJ's organization guide](https://blog.pioneerdj.com/djtips/how-to-manage-your-music-like-a-pro/).

The engine currently does not compute BPM, musical key, genre or energy, edit native cues, or implement native app automation. Its XML follows [AlphaTheta's published interchange specification](https://cdn.rekordbox.com/files/20200410160904/xml_format_list.pdf); that specification is not a headless native USB export API.

The [organization commands](QUICKSTART.md#keep-notes-and-sort-explicit-selections) can read existing tagged BPM/key and save supplied notes, tags, roles and analysis provenance against catalog byte revisions. Use them to build ordered, overlapping collections before planning delivery. Known tags remain unverified until explicitly reviewed. Delivery carries frozen supplied values into working-copy tags/comments for the app to read; verify the actual native display. It does not set native cues, and native analysis databases or changed working tags are not automatically adopted into catalog annotations.

## Release acceptance gate

For app-only readiness, retain the actual app version, frozen IDs and playlist/crate counts, preparation outcomes, native import/analysis observations, fresh working-file checks and observed load/play scope. A player model or USB is not part of this app-only acceptance gate. Exercise both native apps before claiming both app workflows are validated.

For USB delivery, additionally retain the player/controller model and firmware, volume identity/filesystem/partition information, native export/copy and device-library observations, readback results, and physical playback scope. Exercise at least one real target through the native app; a filesystem fixture or mocked observation cannot replace it. Test both USB workflow providers separately before claiming both are validated. See [status](STATUS.md) for what has actually been observed.
