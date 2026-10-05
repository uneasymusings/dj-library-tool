# Version-one command contract

This document describes **`0.1.0a7`**: 42 MCP tools plus CLI and authenticated local HTTP routes. Released a6 exposes 40 tools (without `djlib_collect_request` and `djlib_import_rekordbox_analysis`); earlier a3 exposes 34; a2 omits delivery/request/organization commands. The response envelope remains schema version `1`. See [status](STATUS.md) for validation and publication evidence.

## Envelope

All application results use:

```json
{
  "schema_version": "1",
  "ok": true,
  "request_id": "req_unique-id",
  "result": {},
  "warnings": [],
  "error": null
}
```

Failures set `ok: false`, `result: null`, and `error: {code, message, retryable}`. Request IDs identify calls; job IDs identify accepted work. `warnings` is reserved and currently empty. Quality evidence appears in asset properties/manifests; an empty warnings array is not a quality guarantee.

CLI stdout is this JSON envelope whenever output is captured (pipes, files, assistants, CI), with the global `--json` flag (accepted before or after the subcommand), or with `DJLIB_OUTPUT=json`. In an interactive terminal, commands print readable views instead: tables, live job progress on stderr and copy-pasteable next steps; errors go to stderr. `DJLIB_OUTPUT=pretty` forces the terminal view. Presentation never changes what a command submits, and generated assistant sessions set `DJLIB_OUTPUT=json`. Help and argument parsing follow Typer conventions.

Interactive terminals add conveniences that scripts never get implicitly: `--key` may be omitted for `scan`, `start`, `export` and `delivery prepare` (a fresh key is generated; with JSON output a missing key is an `INPUT_INVALID` envelope), submissions follow their job until it finishes (Ctrl-C detaches; the job keeps running), and `delivery observe ID` without `--file` asks what you saw and fills counts and recording IDs from the frozen, checksummed manifest only after you confirm. `scan` without a path uses the only allowed root, in either mode. MCP stdout is protocol-only. Workspace service diagnostics go to `runtime/service.log`. The service has no unauthenticated browser UI or public endpoint.

## Inputs

Requests are Pydantic contracts with unknown fields rejected. `djlib schemas` emits current machine-readable JSON Schemas without starting the service. The checked-in [schema artifact](../schemas/inputs.json) is generated from the same classes.

| Contract | Purpose |
| --- | --- |
| `CollectionRequest` | Name, profile, explicit local tracks with path/artist/title/version. |
| `StartRequest` | Existing plan ID, revision, submission key. |
| `DownloadRequest` | Name, selected source tracks with URL/artist/title/version, submission key. |
| `ScanRequest` | Allowed directory, submission key. |
| `ResolveRequest` | Current review revision and chosen resolution. |
| `ExportRequest` | Collection ID and submission key. |
| `DeviceRequest` | Mounted path and estimated bytes needed; read-only effect. |
| `DeliveryRequest` | Name, existing collection IDs, workflow, app version, target profile, audio mode, and pilot/full selection. |
| `DeliveryPrepareRequest` | Current delivery revision and preparation submission key. |
| `DeliveryDeviceRequest` | Current delivery revision and exact mounted volume path to bind read-only. |
| `DeliveryObservation` | Current revision, observed stage, app version, track/playlist coverage, checked recording IDs, observer, notes, method, outcome, and optional actual hardware details. |
| `DeliveryVerifyRequest` | Current delivery revision for working-file verification or audio-hash readback on the bound volume. |
| `DeliveryNativeXMLRequest` | Current delivery revision and allowed native rekordbox XML path; read-only snapshot comparison. |
| `RequestCreate` | Stable key, list name and up to 1,000 exact named requests or unknown IDs with evidence. |
| `RequestRefresh` | Current request revision and optional selected item IDs for bounded file checks. |
| `RequestResolution` | Current revision and explicit source, catalog identity selection or clear action. |
| `AnnotationRequest` | Exact recording/byte revision, annotation revision, stable key and supplied field patch. |
| `OrganizationRequest` | Stable key, explicit catalog references, filters, unknown policy and ordering. |
| `RootsRequest` | One to 100 existing directory paths, explicitly added to allowed roots. |
| `ReconcileRequest` | Stable key and up to 1,000 distinct changed paths, each pinning an old revision, current new SHA-256 and `tag_only` or `replace_audio` action. |

Labels reject control characters and blank artist/title. Named identity normalizes Unicode/case/spacing while retaining versions and symbol-only distinctions. A mix name written inside the title is equivalent to the same name in the version field: “Rain (Extended Mix)” with no version matches title “Rain” plus version “Extended Mix” (`identity_match: equivalent_labels` in request candidates). Identical bytes cataloged under equivalent labels reuse that recording instead of failing with `EXISTING_IDENTITY_CONFLICT`; a different version still conflicts. Stored identity keys are unchanged. Incomplete-label scans use provisional byte identity; WAV artist/title come from ID3 or, when absent, the RIFF INFO chunk. Scans skip djlib's own workspace folders and links that resolve outside allowed roots, and report the counts in the scan job's `result.skipped_files`. These are conservative catalog rules, not an acoustic identity resolver or automatic repair of historical merges. Input errors identify invalid field locations and generic reasons without echoing submitted values.

### Delivery inputs

`DeliveryRequest` accepts 1–100 distinct `collection_ids` and freezes their accepted recording IDs, byte revisions, labels, and membership. Repeated recordings are prepared once while retaining each collection's membership. Different byte revisions for the same recording return `DELIVERY_REVISION_CONFLICT`; delivery does not silently select one. The combined selection is limited to 10,000 unique recordings.

- `workflow`: `rekordbox_import`, `serato_import`, `rekordbox_usb` or `serato_portable`.
- `app_version`: required. `hardware_profile` is required only for `rekordbox_usb` and must be returned by `delivery targets`; the other workflows reject that field. App-only import/analysis needs no USB or player/controller model.
- `audio_mode`: `preserve` (default), `mp3_320`, or `wav16_44100`. Preparation always creates separate working copies; conversion does not improve source fidelity.
- `phase`: `pilot` (default) or `full`. `pilot_size` is 1–5, default 3; sampling visits the selected collections in round-robin order.
- Full local preparation may omit `pilot_delivery_id`, explicitly reporting unvalidated local preparation. If supplied, the pilot must be completed and match workflow, hardware profile, audio mode and app version; otherwise preparation returns `PILOT_REQUIRED`. All native/device readiness gates remain. Full copies do not inherit native cues or analysis from separate pilot paths.

`DeliveryObservation.stage` is one of `imported`, `analyzed`, `native_exported`, `device_library_checked`, or `hardware_playback`. Passed observations require preceding stages, the planned app version, exact unique-track and per-collection counts, and every checked recording ID. Only hardware playback for a full delivery permits a nonempty sample; a pilot requires playback of every selected recording. `playlist_counts` uses collection IDs as keys. Failed observations may report incomplete or zero coverage and invalidate later observations.

`method` is `native_app_ui` for the first four stages and `physical_hardware` for playback. `outcome` is `passed` or `failed`; `observer` and `notes` are required. These observations remain operator-reported evidence. Passed rekordbox hardware playback additionally requires matching `hardware_profile`, nonblank `firmware_version`, and `storage_recognized: true`; missing details return `HARDWARE_DETAILS_REQUIRED`. CDJ-3000 firmware 3.30, including normalized whitespace/`v` prefixes, returns `FIRMWARE_UNSUPPORTED`.

App-only workflows accept only `imported` and `analyzed` observations, followed by `verify-app`. Successful verification jobs return an evidence-commit receipt. Read delivery status for `app_requirements_met_at_last_check` and `evidence.app_readback.checked_at`; these retain historical, operator-conditional evidence and never assert fresh app/USB readiness. USB workflows require their device/native/hardware stages and fresh `verify-device`. A native XML inspection checks a rekordbox snapshot without changing any stage or readiness; the input path must be authorized, and product-version mismatches remain explicit.

### Request and organization inputs

`requests create --text FILE` builds a `RequestCreate` from a plain tracklist in the CLI: one `Artist - Title (Mix)` per line, with numbering, bullets, timestamps and a trailing `[LABEL]` removed (a bracketed mix name is kept). `ID - ID` lines become unknown items when they carry a timestamp or `--source` URL. Leading unnumbered lines of a numbered list are headings; the first names the list. Lines that are skipped are reported in the envelope's `warnings`.

`RequestCollect` (`revision`, optional `name`) queues an `organize` job over the request's `satisfied` items in list order, deduplicated by byte revision; nothing else is included and an empty selection is `COLLECTION_EMPTY`. Request candidates may include up to five `different_version` recordings of the same song (same artist and base title) for context; they never satisfy, disambiguate or truncate a match.

`AnalysisImport` (`path`) reads a rekordbox Collection XML export from an allowed folder or the workspace (the CLI copies the chosen file into the workspace first). Tracks match only by exact file location: catalog originals or prepared delivery working copies. Known `AverageBpm`/`Tonality` values are stored as BPM/key annotations with `source: "rekordbox_analysis"` and `verified: false`; values with any other source are kept. Library rows and collection pages include `dj: {bpm, key, bpm_source, key_source, energy, set_role}` from saved annotations.

`rekordbox push` runs in the CLI process, which macOS lets drive another app once the terminal is allowed under Accessibility; the coordinator never automates UI. For each collection it exports (hash-checked) and copies the M3U8 to `exports/rekordbox/<collection name>.m3u8` (only characters illegal in file names are replaced; rekordbox names the playlist after the file), skips names already listed in rekordbox, and chooses the file in File > Import > Import Playlist. Presence is confirmed by reading rekordbox's Track > Add To Playlist menu without focusing it. `--verify` performs one File > Export Collection in xml format and adds `entries/expected/matched/analyzed` per crate; `--when-idle S` waits until there has been no keyboard or mouse input for S seconds; `--request ID` first queues `requests collect`. The result lists `crates` (`collection_id`, `playlist`, `status: imported|already_in_rekordbox`, `playlist_found`), `rekordbox_ui_seconds`, `analysis_sync`, `verified_by` and `database_modified_directly: false`. Dialogs are driven through accessibility values and named buttons; the two shortcut keystrokes are sent only after confirming rekordbox is frontmost and the expected dialog has focus, otherwise the dialog is cancelled with `APP_DIALOG_FAILED`. Missing permission returns `APP_AUTOMATION_NOT_ALLOWED`; non-macOS returns `APP_AUTOMATION_UNSUPPORTED`.

`AnalysisImport.path` is optional. Without it, the coordinator reads rekordbox's analysis folder (macOS `~/Library/Pioneer/rekordbox/share/PIONEER/USBANLZ`, Windows `%APPDATA%\Pioneer\rekordbox\share\PIONEER\USBANLZ`), parsing only files whose size or modification time changed. Each `ANLZ0000.DAT` contributes its file name, median beat-grid tempo and hot/memory cue counts; a track matches only when exactly one catalog recording has that file name (originals or prepared working copies), and the newest analysis per name wins. BPM is stored like XML values (`rekordbox_analysis`, unverified, never replacing other sources) and cue counts under the annotation key `rekordbox`. Key is not included (`key_included: false`). Once a workspace has synced, the coordinator repeats the sync every two minutes. `NATIVE_ANALYSIS_UNAVAILABLE` means no analysis folder exists.

Scans label untagged files from their names (`03 - Artist - Title (Mix)` → artist and title) while keeping provisional byte identity (`identity_evidence.labels_source: file_name`); a rescan relabels an earlier `Unknown artist` provisional recording without changing its identity or memberships. Request matching also considers provisional recordings whose labels are equivalent, reported as `identity_match: file_name_labels`.

A coordinator started implicitly by a command is launched with `--idle-exit 1800` and exits after 30 minutes without requests or queued/running jobs; `service start` launches one without idle exit.

`delivery plan` also accepts `--collection ID` (repeatable), `--workflow`, `--app-version`, optional `--name`, `--player` and `--full` instead of `--file`.

Named request items preserve artist/title/version; unknown items retain a label plus timestamp or HTTPS source evidence without guessed identity. Saved reads are paginated snapshots (`next_offset` maps to `after`); explicit create/refresh/resolution performs bounded checks. Source selection is separate from acquisition. Multiple exact byte revisions require explicit selection. See [request recipes](../skills/dj-library/references/cli.md#exact-requests-and-unknown-ids).

Annotations bind exact recording/revision IDs. Revision `0` creates the first annotation; later patches require the current revision. Omitted fields are unchanged and explicit null clears the annotation. BPM/key values carry `source: operator|native_tag` and `verified: false` by default; `native_tag` must match freshly read catalog tags. Organization freezes ordered collections from up to 1,000 explicit references, reports exclusions and uses explicit unknown policies. It does not perform acoustic analysis or retag catalog originals. See [organization recipes](../skills/dj-library/references/cli.md#catalog-notes-and-ordered-collections).

## Use cases and routes

| CLI | MCP tool | HTTP |
| --- | --- | --- |
| `capabilities` | `djlib_capabilities` | `GET /capabilities` |
| `plan --file FILE` | `djlib_plan_collection` | `POST /plans` |
| `start ID --revision N --key KEY` | `djlib_start` | `POST /jobs` |
| `scan PATH --key KEY` | `djlib_scan` | `POST /scans` |
| `download --file FILE` | `djlib_download` | `POST /downloads` |
| `source-inspect URL` | `djlib_source_inspect` | `POST /sources/inspect` |
| `jobs list/get/items` | `djlib_jobs/job/items` | `GET /jobs`, `/jobs/{id}`, `/jobs/{id}/items` |
| `jobs watch ID` | CLI only | polls `GET /jobs/{id}` until it leaves queued/running |
| `jobs events ID` | CLI only | `GET /jobs/{id}/events` |
| `jobs control ID ACTION` | `djlib_control` | `POST /jobs/{id}/control` |
| `reviews list/resolve` | `djlib_reviews/resolve` | `GET /reviews`, `POST /reviews/{id}` |
| `library --query QUERY` | `djlib_library` | `GET /library` |
| `collections --query QUERY` | `djlib_collections` | `GET /collections` |
| `requests list --query QUERY` | `djlib_requests` | `GET /requests` |
| `delivery list --query QUERY` | `djlib_deliveries` | `GET /deliveries` |
| `roots list` | `djlib_roots` | `GET /roots` |
| `roots add PATH...` | `djlib_add_roots` | `POST /roots` |
| `reconcile --file FILE` | `djlib_reconcile` | `POST /reconciliations` |
| `collection ID` | `djlib_collection` | `GET /collections/{id}` |
| `export ID --key KEY` | `djlib_export` | `POST /exports` |
| `usb-preflight PATH` | `djlib_usb_preflight` | `POST /devices/preflight` |
| `delivery targets` | `djlib_delivery_targets` | `GET /delivery-targets` |
| `delivery plan --file FILE` | `djlib_plan_delivery` | `POST /deliveries` |
| `delivery get ID` | `djlib_delivery` | `GET /deliveries/{delivery_id}` |
| `delivery prepare ID --revision N --key KEY` | `djlib_prepare_delivery` | `POST /deliveries/{delivery_id}/prepare` |
| `delivery bind-device ID PATH --revision N` | `djlib_bind_delivery_device` | `POST /deliveries/{delivery_id}/device` |
| `delivery observe ID --file FILE` | `djlib_observe_delivery` | `POST /deliveries/{delivery_id}/observations` |
| `delivery verify-device ID --revision N` | `djlib_verify_delivery_device` | `POST /deliveries/{delivery_id}/verify` |
| `delivery verify-app ID --revision N` | `djlib_verify_delivery_app` | `POST /deliveries/{delivery_id}/verify-app` |
| `delivery inspect-native-xml ID PATH --revision N` | `djlib_inspect_delivery_native_xml` | `POST /deliveries/{delivery_id}/native-xml` |
| `requests create --file FILE` | `djlib_create_request` | `POST /requests` |
| `requests get ID` | `djlib_request` | `GET /requests/{id}` |
| `requests refresh ID --revision N` | `djlib_refresh_request` | `POST /requests/{id}/refresh` |
| `requests resolve ID ITEM_ID --file FILE` | `djlib_resolve_request` | `POST /requests/{id}/items/{item_id}` |
| `requests report ID --revision N` | `djlib_request_report` | `POST /requests/{id}/report` |
| `requests collect ID [--name NAME] [--revision N]` | `djlib_collect_request` | `POST /requests/{id}/collection` |
| `import-rekordbox XML` | `djlib_import_rekordbox_analysis` | `POST /analysis/rekordbox` |
| `rekordbox push [ID...] [--request ID] [--verify] [--when-idle S]` | CLI only (macOS desktop session) | `POST /exports`, then `POST /analysis/rekordbox` |
| `rekordbox sync` | `djlib_import_rekordbox_analysis` (no path) | `POST /analysis/rekordbox` with `{"path": null}` |
| `rekordbox pull` | CLI only (macOS desktop session) | `POST /analysis/rekordbox` with an XML path |
| `organize metadata ID --asset-revision-id REV_ID` | `djlib_track_metadata` | `GET /recordings/{id}/metadata` |
| `organize get ID --asset-revision-id REV_ID` | `djlib_annotations` | `GET /recordings/{id}/annotations` |
| `organize annotate --file FILE` | `djlib_annotate` | `POST /annotations` |
| `organize collection --file FILE` | `djlib_organize` | `POST /organization` |

`djlib ui` starts or reuses the coordinator and returns `{"url": "http://127.0.0.1:PORT/ui/#token=…"}`; in a terminal it also opens the review page unless `--no-open`, and JSON output never opens a browser. The page's three static files (`GET /ui/`, `/ui/app.js`, `/ui/app.css`) are the only routes served without the bearer token; they contain no catalog data and carry a strict Content-Security-Policy (no inline code, same-origin connections only), `nosniff`, `no-referrer` and `no-store`. The page reads the token from the URL fragment (never sent to the server), keeps it in tab-scoped session storage, removes it from the address bar and calls the same authenticated JSON routes listed above. Its only writes are request-item `satisfy` resolutions and building a crate from owned request items.

`init`, `doctor`, `version`, `schemas`, and `setup-agent --output PATH` are local CLI operations. Session setup requires an initialized workspace and a new output folder outside it; it copies the skill and explicit MCP configuration without credentials or personal configuration changes. Session timeouts are transport ceilings, not job deadlines. Existing sessions are unchanged. `demo` combines original tones and normal use cases. `service status` does not start a coordinator; `service start` explicitly starts/discovers it, and `service stop` checkpoints work and exits. Windows MCP cold start returns `COORDINATOR_START_REQUIRED`; generated `launch.py` pre-starts the service before the host, or manual configurations require external-terminal startup. Other supported client/platform paths retain coordinator startup. The Windows process-job boundary is preserved, not escaped.

## Bounds and paging

- Job/item/library limits: 1–100; defaults 20. Collection default 50, maximum 100.
- Collection `after` is a zero-based offset; initial value 0.
- Items `after` is the last position returned; initial value -1.
- Events `after` is the last event cursor returned; initial value 0.
- `next_cursor: null` ends a collection/item/event page.
- Queries match every word, ignoring case and accents (“bjork radio” finds “Björk … (Radio Edit)”). Library rows are ordered by artist, title, version, then IDs; saved lists newest first. Job queries also match the job kind.
- Library, jobs and saved collection/request/delivery lists accept a query and opaque `after` cursor. Reuse `next_cursor` with the same listing/query; a null cursor ends paging. Results include total and an insertion cutoff, with current mutable metadata/locations. Library groups each recording/revision and all recorded locations; availability is not freshly checked. Reviews remain a bounded view without a continuation cursor.
- Plan replies include a ten-track preview; full input is persisted privately.
- Source descriptions are limited to 30,000 characters and chapters to 500; output declares description truncation.
- Current collection cap: 10,000 local tracks; selected URL download cap: 1,000 tracks.

## Exit and outcome semantics

| Exit | Meaning |
| --- | --- |
| 0 | Command succeeded or job intent accepted; acceptance does not imply completion. |
| 2 | Invalid input or application error; inspect the envelope. |
| 3 | `jobs wait` deadline reached; job continues in the coordinator. |
| 4 | `jobs wait`/`watch` returned failure, cancellation, a pause, required attention, or completion with gaps. |
| 130 | Interrupted in a terminal (Ctrl-C); accepted jobs keep running. |

`jobs wait` is a convenience with a maximum 60-second polling window; `jobs watch` waits until the job leaves queued/running. In a terminal, submissions that follow their job use the same exit codes. MCP clients should poll `djlib_job` and respect `next_poll_after_seconds`. To inspect all items in a bulk job, page through outcomes instead of repeatedly loading the whole input.

## Recovery and readiness

`TRANSPORT_UNCERTAIN` means a mutation may have been accepted. For keyed submissions, resubmit the identical request with its original idempotency key. `IDEMPOTENCY_CONFLICT` means the key belongs to another intent. `PLAN_STALE`, `REVIEW_STALE`, and `DELIVERY_STALE` require reading the latest state before choosing another action.

Startup is a separate boundary: `SERVICE_START_BUSY` means the startup lock timed out; `SERVICE_START_FAILED` means the child exited without a discovered healthy service; `SERVICE_START_TIMEOUT` means the child was still running after the readiness window. These errors submit no music operation. Read `service status` and the indicated log before retrying, especially on timeout. Windows MCP `COORDINATOR_START_REQUIRED` requires external-terminal `service start` or the generated launcher, not another cold-start MCP call.

Delivery planning creates a new frozen delivery record on each call; it has no idempotency-key field. Preparation is keyed: intent, the delivery-to-job binding, and items are committed before media work. Concurrent preparation requests for the same frozen delivery share one job, including requests with different fresh keys; each key is bound to that job. A key already naming another request returns `IDEMPOTENCY_CONFLICT`; a different preparation payload for an already-bound delivery returns `DELIVERY_CONFLICT`.

Organization and catalog reconciliation return durable jobs; supplied keys replay the original intent. Passed `analyzed` and `native_exported` observations plus `verify-app` also return jobs, deriving an idempotency key from delivery ID, revision, operation and full observation. Repeating the same intent returns the same job after a lost response; use the latest revision for a new check. Other observations, device binding and device verification remain direct revision-checked operations. Evidence updates use atomic compare-and-swap; deferred job finalizers also fence against stale generations and paused/cancelled work. Inspect failed items and reread delivery evidence after a failure, since failure invalidation can advance the revision.

A completed verification job returns only `delivery_id`, `revision` and `evidence_committed: true`. It never returns `ready_for_app_use`. Read the delivery for `app_requirements_met_at_last_check`, `evidence.app_readback.checked_at` (also `last_app_readback_at`) and blockers. These are saved evidence, conditional on operator-reported app behavior. Use the current delivery revision to submit a new check when freshness matters. Passed native-export observations verify exact analyzed bytes per item and bound-volume identity at acceptance/finalization; they do not perform the native export.

Exports report `app_state: prepared_for_import` and `device_state: not_exported`. Generic collection replies use `not_tracked_here`; they do not infer either native success or failure. Delivery evidence is separate. A delivery with partial preparation publishes no native import handoff. Neither preparation nor native-library filenames establish app import, native export, or playback.

`delivery get` combines historical observations/readback with a current lightweight volume-identity and marker check. Its `requirements_met_at_last_check` may be true, but `hashes_rechecked_by_status` is false and `ready_for_departure` remains false. `last_audio_readback_at` identifies the stored readback time. Only `delivery verify-device` can return `ready_for_departure: true`, after fresh matching audio hashes and all native, device, and hardware requirements pass. This is attributed operator evidence plus machine byte readback, not automatic player verification: `hardware_verified_automatically` and `native_automation_available` remain false.

Rebinding a device invalidates device-dependent evidence. Re-observing an earlier stage invalidates later stages; detected changes to or loss of analyzed working files invalidate analysis and subsequent readiness. Readback checks do not write the USB or interpret native database contents. See [the delivery workflow](DJ_DELIVERY.md) for the supported app steps and remaining hardware boundary.
