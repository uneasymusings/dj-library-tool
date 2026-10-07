# Delivery: working copies for rekordbox or Serato

`djlib set` and `rekordbox push|usb` use the user's original files and are the usual path. Use a delivery instead when the user wants separate, tagged working copies (for example with notes or BPM/key written into the tags), uses Serato, or wants a step-by-step record of a native import and a USB export. Delivery tools are in the full MCP profile (`DJLIB_MCP_TOOLS=full`); the CLI group is `djlib delivery`. Serato working copies are supported but untested with real music.

| Workflow | For | Needs |
| --- | --- | --- |
| `rekordbox_import`, `serato_import` | The app's own library | Collection IDs and the installed app version; no USB or player model |
| `rekordbox_usb` | A stick for CDJ/XDJ players | Also the exact `hardware_profile` (see `delivery targets`) and the bound USB |
| `serato_portable` | Another Serato computer | No hardware profile |

## Plan and prepare

Read the app version from its About screen or a native XML export. Bundle metadata such as `CFBundleVersion` can be a build number; don't substitute it. Keep a mismatch visible.

```json
{
  "name": "Local app pilot",
  "collection_ids": ["COLLECTION_ID"],
  "workflow": "rekordbox_import",
  "app_version": "7.2.19",
  "audio_mode": "preserve",
  "phase": "pilot",
  "pilot_size": 3
}
```

```bash
djlib delivery targets
djlib delivery plan --file app-pilot.json
djlib delivery prepare DELIVERY_ID --revision CURRENT --key app-pilot-v1
djlib jobs wait JOB_ID --timeout 30
djlib delivery get DELIVERY_ID
```

- Start with a small pilot. A `full` delivery may omit the pilot ID (recorded as unvalidated); a supplied pilot must match and pass.
- `audio_mode` defaults to `preserve`. Use `mp3_320` or `wav16_44100` only for a documented player limit; conversion never improves quality.
- Preparation writes separate working copies with the frozen annotations (BPM/key/genre, notes, tags, role, energy) in their tags, plus named M3U8 playlists, `delivery-manifest.json` and `NATIVE_STEPS.txt`. Originals are untouched. Later annotation changes need a new plan.
- MP4's integer BPM tag can't hold a fraction: the exact value stays in a freeform tag and the manifest, with a warning.

## Native steps and observations

Follow `NATIVE_STEPS.txt`. For rekordbox: File > Import > Import Playlist with the M3U8, then analyze the new copies. For Serato: import the working files, make regular crates and analyze them. Don't reanalyze libraries the user has edited by hand, and never edit an app's database directly.

Record each stage only after checking it in the app: `delivery observe DELIVERY_ID --file observation.json` (MCP `djlib_observe_delivery`). Fields: `revision`, `stage`, actual `app_version`, `track_count`, `playlist_counts` by collection ID, `checked_recording_ids`, `observer`, `notes`, `method` and `outcome`. Check `djlib schemas` for the exact constraints.

- Stages: `imported`, `analyzed`, `native_exported`, `device_library_checked` (method `native_app_ui`) and `hardware_playback` (method `physical_hardware`).
- Record failures and partial counts as they are; never manufacture a pass.
- A passed `analyzed` or `native_exported` observation queues a check job. Wait for it, then read the new delivery revision before the next stage.

## App verification

After `imported` and `analyzed`: `delivery verify-app DELIVERY_ID --revision CURRENT` (MCP `djlib_verify_delivery_app`) queues a job. Success returns only `delivery_id`, `revision` and `evidence_committed`. Read `delivery get` for `app_requirements_met_at_last_check`, blockers and `evidence.app_readback.checked_at`: saved, operator-conditional evidence, not a fresh check and not USB readiness. App-only work ends here.

For rekordbox membership, export the collection (File > Export Collection in xml format) into the workspace and run `delivery inspect-native-xml DELIVERY_ID PATH --revision CURRENT` (MCP `djlib_inspect_delivery_native_xml`). It compares paths, playlist membership and order, and the declared version, read-only. It never changes a stage or proves analysis accuracy, loading or USB export. To use its BPM/key for sorting, annotate the recording with `source: "operator"`, the XML checksum and version in the notes, and `verified: false`.

## USB delivery

1. `delivery bind-device DELIVERY_ID /Volumes/NAME --revision CURRENT` binds the exact volume, read-only.
2. Export through the app: rekordbox's Devices with the player's Device Library or OneLibrary, or Serato's Files panel for regular crates. Record `native_exported` and `device_library_checked`.
3. `delivery verify-device DELIVERY_ID --revision CURRENT` checks the prepared audio hashes on the stick without writing it.
4. Test every pilot track on the player and record `hardware_playback` with the matching `hardware_profile`, the actual `firmware_version` and `storage_recognized: true`. CDJ-3000 firmware 3.30 can't pass.
5. Reconnect and verify the device again before departure. `ready_for_departure` needs that fresh check plus all native and hardware evidence.

djlib never formats, ejects or writes a USB device. Ask for the player or controller model before recommending a filesystem or export format, and don't suggest formatting as a routine step. Pilot and full copies use different paths, so cues and analysis are not reused automatically.

## Report

Say which stages were observed by the user, which were checked by djlib and which remain: acquired, cataloged, prepared, imported, analyzed, app-verified, exported, device-verified and hardware-tested are different things. A plain file copy is a fallback, never a DJ device export.
