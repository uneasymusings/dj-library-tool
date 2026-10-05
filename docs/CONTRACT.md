# Version-one command contract

This document describes **`0.1.0a4`**: 40 MCP tools plus JSON CLI and authenticated local HTTP routes. Earlier a3 exposes 34 tools; a2 omits delivery/request/organization commands. The response envelope remains schema version `1`. See [status](STATUS.md) for validation and publication evidence.

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

CLI stdout is JSON for application commands; shell/framework help and argument parsing follow Typer conventions. MCP stdout is protocol-only. Workspace service diagnostics go to `runtime/service.log`. The service has no unauthenticated browser UI or public endpoint.

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

Labels reject control characters and blank artist/title. Named identity normalizes Unicode/case/spacing while retaining versions and symbol-only distinctions. Incomplete-label scans use provisional byte identity. These are conservative catalog rules, not an acoustic identity resolver or automatic repair of historical merges. Input errors identify invalid field locations and generic reasons without echoing submitted values.

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
| `organize metadata ID --asset-revision-id REV_ID` | `djlib_track_metadata` | `GET /recordings/{id}/metadata` |
| `organize get ID --asset-revision-id REV_ID` | `djlib_annotations` | `GET /recordings/{id}/annotations` |
| `organize annotate --file FILE` | `djlib_annotate` | `POST /annotations` |
| `organize collection --file FILE` | `djlib_organize` | `POST /organization` |

`init`, `doctor`, `version`, `schemas`, and `setup-agent --output PATH` are local CLI operations. Session setup requires an initialized workspace and a new output folder outside it; it copies the skill and explicit MCP configuration without credentials or personal configuration changes. Session timeouts are transport ceilings, not job deadlines: accepted background work survives client exit. Existing generated sessions are unchanged. `demo` combines original tone generation and normal use cases. `service status` does not start a coordinator; normal requests do. `service stop` checkpoints work and exits the coordinator.

## Bounds and paging

- Job/item/library limits: 1–100; defaults 20. Collection default 50, maximum 100.
- Collection `after` is a zero-based offset; initial value 0.
- Items `after` is the last position returned; initial value -1.
- Events `after` is the last event cursor returned; initial value 0.
- `next_cursor: null` ends a collection/item/event page.
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
| 4 | `jobs wait` returned failure, cancellation, required attention, or completion with gaps. |

`jobs wait` is a convenience with a maximum 60-second polling window. MCP clients should poll `djlib_job` and respect `next_poll_after_seconds`. To inspect all items in a bulk job, page through outcomes instead of repeatedly loading the whole input.

## Recovery and readiness

`TRANSPORT_UNCERTAIN` means a mutation may have been accepted. For keyed submissions, resubmit the identical request with its original idempotency key. `IDEMPOTENCY_CONFLICT` means the key belongs to another intent. `PLAN_STALE`, `REVIEW_STALE`, and `DELIVERY_STALE` require reading the latest state before choosing another action.

Delivery planning creates a new frozen delivery record on each call; it has no idempotency-key field. Preparation is keyed: intent, the delivery-to-job binding, and items are committed before media work. Concurrent preparation requests for the same frozen delivery share one job, including requests with different fresh keys; each key is bound to that job. A key already naming another request returns `IDEMPOTENCY_CONFLICT`; a different preparation payload for an already-bound delivery returns `DELIVERY_CONFLICT`.

Organization and catalog reconciliation return durable jobs; supplied keys replay the original intent. Passed `analyzed` and `native_exported` observations plus `verify-app` also return jobs, deriving an idempotency key from delivery ID, revision, operation and full observation. Repeating the same intent returns the same job after a lost response; use the latest revision for a new check. Other observations, device binding and device verification remain direct revision-checked operations. Evidence updates use atomic compare-and-swap; deferred job finalizers also fence against stale generations and paused/cancelled work. Inspect failed items and reread delivery evidence after a failure, since failure invalidation can advance the revision.

A completed verification job returns only `delivery_id`, `revision` and `evidence_committed: true`. It never returns `ready_for_app_use`. Read the delivery for `app_requirements_met_at_last_check`, `evidence.app_readback.checked_at` (also `last_app_readback_at`) and blockers. These are saved evidence, conditional on operator-reported app behavior. Use the current delivery revision to submit a new check when freshness matters. Passed native-export observations verify exact analyzed bytes per item and bound-volume identity at acceptance/finalization; they do not perform the native export.

Exports report `app_state: prepared_for_import` and `device_state: not_exported`. Generic collection replies use `not_tracked_here`; they do not infer either native success or failure. Delivery evidence is separate. A delivery with partial preparation publishes no native import handoff. Neither preparation nor native-library filenames establish app import, native export, or playback.

`delivery get` combines historical observations/readback with a current lightweight volume-identity and marker check. Its `requirements_met_at_last_check` may be true, but `hashes_rechecked_by_status` is false and `ready_for_departure` remains false. `last_audio_readback_at` identifies the stored readback time. Only `delivery verify-device` can return `ready_for_departure: true`, after fresh matching audio hashes and all native, device, and hardware requirements pass. This is attributed operator evidence plus machine byte readback, not automatic player verification: `hardware_verified_automatically` and `native_automation_available` remain false.

Rebinding a device invalidates device-dependent evidence. Re-observing an earlier stage invalidates later stages; detected changes to or loss of analyzed working files invalidate analysis and subsequent readiness. Readback checks do not write the USB or interpret native database contents. See [the delivery workflow](DJ_DELIVERY.md) for the supported app steps and remaining hardware boundary.
