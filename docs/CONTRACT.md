# Version-one command contract

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

Labels reject control characters and blank artist/title. Recording normalization uses Unicode NFKC, casefolding, and punctuation/whitespace normalization; it retains version words. This is text matching, not an acoustic identity resolver.

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
| `library QUERY` | `djlib_library` | `GET /library` |
| `collection ID` | `djlib_collection` | `GET /collections/{id}` |
| `export ID --key KEY` | `djlib_export` | `POST /exports` |
| `usb-preflight PATH` | `djlib_usb_preflight` | `POST /devices/preflight` |

`init`, `doctor`, `version`, `schemas`, and `setup-agent --output PATH` are local CLI operations. Session setup requires an initialized workspace and a new output folder outside it; it copies the portable skill and explicit MCP configuration, without copying credentials or editing personal host configuration. `demo` combines synthetic file generation and normal use cases. `service status` does not start a coordinator; normal requests do. `service stop` checkpoints the current worker and exits the coordinator.

## Bounds and paging

- Job/item/library limits: 1–100; defaults 20. Collection default 50, maximum 100.
- Collection `after` is a zero-based offset; initial value 0.
- Items `after` is the last position returned; initial value -1.
- Events `after` is the last event cursor returned; initial value 0.
- `next_cursor: null` ends a collection/item/event page.
- Recent jobs, reviews, and label search are bounded views but do not yet expose continuation cursors.
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

`TRANSPORT_UNCERTAIN` means a mutation may have been accepted. Resubmit the identical request with its original idempotency key. `IDEMPOTENCY_CONFLICT` means the key belongs to another intent. `PLAN_STALE` and `REVIEW_STALE` require reading the latest state before choosing another action.

Exports report `app_state: prepared_for_import` and `device_state: not_exported`. A collection reports `not_imported` until a future integration provides actual evidence. The first release has no operation that establishes `device_ready`.
