# Contributing

Start with [status](docs/STATUS.md), [architecture](docs/ARCHITECTURE.md), and the [product plan](PLAN.md). This is an experimental local engine; useful improvements should advance an actual DJ or assistant workflow.

## Environment

Python 3.12+; uv; FFmpeg/ffprobe for compressed audio; optional yt-dlp download extra.

```bash
uv sync --locked --extra download
uv run pytest -q
uv run ruff check src scripts tests
uv run ruff format --check src scripts tests
uv build
```

Use a separate initialized workspace and synthetic or appropriately licensed media. Never commit personal paths, workspace databases, credentials, source downloads, or copyrighted audio fixtures. The built-in demo generates original tones.

## Design conventions

- Keep business behavior in application/worker modules, with thin CLI and MCP adapters.
- Use strict versioned request contracts and typed failures; avoid hidden interactive prompts.
- Persist intent before expensive work. Keep database transactions short and all slow I/O outside them.
- Preserve semantic idempotency and distinguish acceptance, partial completion, app import, and device readiness.
- Treat source metadata as untrusted data. Preserve provenance and recording/version evidence.
- Use comments for operational invariants and non-obvious tradeoffs; let ordinary names and functions explain straightforward mechanics.
- Document behavioral changes, compatibility limitations, and migration requirements in the same change.

Model changes require a new Alembic revision; do not rewrite a migration already used by others. `alembic.ini` targets an ignored development database. Runtime migrations are packaged with the application and receive the coordinator's existing connection.

After changing public request contracts, regenerate the checked-in schemas with `uv run python scripts/export_schemas.py`. The artifact intentionally omits per-call request IDs so generation stays deterministic.

## Pull requests and issues

Describe the user scenario, intended behavior, implementation choice, actual validation performed, and outstanding limitations. Report facts observed in a real DJ app separately from assumptions based on a file format.

For bug reports, include OS/Python/app versions, command name, error code, and a redacted item/job outcome. Share a synthetic reproducer where possible. Public logs can contain source paths and URLs; redact those first. Use the private reporting process in [SECURITY.md](SECURITY.md) for vulnerabilities.

Runtime tests use original generated audio, isolated workspaces, controlled source responses, actual CLI processes, and real MCP stdio. FFmpeg-dependent cases skip when the decoder is unavailable. Live providers and signed-in AI hosts belong in separate explicit evaluations, not network-dependent CI. Record versions, observed outcomes, and remaining limits in [validation](docs/VALIDATION.md) and [status](docs/STATUS.md). Do not publish personal host transcripts or music libraries.

## GitHub releases

Update the project, engine, and lockfile version together. Update the pinned public install URLs in the README and installation references. Run the full checks above, then:

```bash
uv run --locked python scripts/check_distribution.py
uv run --locked python scripts/release_artifacts.py
```

The second script prints the four files to attach: the current wheel, source archive, portable skill ZIP, and `SHA256SUMS`. It excludes older builds and allows only documented skill resources into the standalone ZIP. The wheel bundles the same skill resources.

Commit and push the reviewed changes; confirm all six matrix jobs pass. Tag that exact commit and create a GitHub prerelease with those four assets and release notes describing observed checks and unfinished scope. Do not upload workspace data or test transcripts. Finally install the published wheel URL into a fresh persistent tool environment outside the checkout, create a new demo/session, and verify its CLI/MCP paths point to the installed package. Test the standalone ZIP download and checksum manifest too. Publication alone does not prove the public install works.
