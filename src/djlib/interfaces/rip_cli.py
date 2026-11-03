"""Explicit YouTube-to-MP3 actions, using the normal download worker and catalog."""

import sys
from typing import Annotated

import typer
from rich.markup import escape

from djlib.domain.contracts import DownloadRequest
from djlib.domain.errors import AppError
from djlib.interfaces import set_sources, terminal
from djlib.interfaces.envelope import envelope
from djlib.interfaces.handles import resolve
from djlib.interfaces.library_cli import all_items, finish_checks
from djlib.interfaces.terminal import follow
from djlib.sources.web import validate_url


def register_rip(app, client, emit, handled, panel=None):
    @app.command("rip", rich_help_panel=panel)
    @handled
    def rip(
        ctx: typer.Context,
        source: Annotated[
            str, typer.Argument(help="A saved set (name or last), or a YouTube video URL.")
        ] = "last",
        artist: str | None = typer.Option(None, help="Artist for a single YouTube video."),
        title: str | None = typer.Option(None, help="Title for a single YouTube video."),
        version: str = typer.Option("", help="Exact mix name for a single video."),
        preview: bool = typer.Option(False, "--preview", help="Show sources without downloading."),
        yes: bool = typer.Option(
            False, "--yes", "-y", help="Download the reviewed sources without prompting."
        ),
    ):
        """Rip a YouTube video or a set's missing tracks as MP3.

        Shows source uploads before asking. Use --preview to inspect them without downloading.

        Noninteractive calls only preview unless --yes is supplied.

        Files enter your library; a saved set is rechecked after the job.
        """
        if preview and yes:
            raise AppError("INPUT_INVALID", "Choose --preview or --yes, not both.")
        local, term = client(ctx), terminal.current()
        request_id = None
        if set_sources.is_url(source):
            if set_sources.provider_of(source) != "YouTube":
                raise AppError(
                    "INPUT_INVALID", "Rip needs a YouTube video URL, or a saved set name."
                )
            validate_url(source)
            if not artist or not title:
                raise AppError(
                    "INPUT_INVALID",
                    "For a video, supply --artist and --title (and --version for a remix).",
                )
            # The supplied URL is a deliberate selection, never a guessed search result.
            page = local.request("POST", "/sources/inspect", data={"url": source})["result"]
            chosen = [
                {
                    "artist": artist,
                    "title": title,
                    "version": version,
                    "label": f"{artist} - {title}" + (f" ({version})" if version else ""),
                    "source": {
                        "url": source,
                        "provider": "youtube",
                        "title": page.get("title"),
                        "uploader": page.get("uploader"),
                    },
                }
            ]
            undecided, name = [], f"{artist} - {title}"
        else:
            if artist or title or version:
                raise AppError(
                    "INPUT_INVALID", "Artist, title and version apply only to a single video URL."
                )
            request_id = resolve(local, "request", source)
            ledger = local.request("GET", f"/requests/{request_id}", params={"limit": 100})[
                "result"
            ]
            chosen, undecided = set_sources.plan_fetch(
                local, all_items(local, ledger), youtube_only=True
            )
            name = ledger["name"]
        result = {
            "name": name,
            "source": source,
            "request_id": request_id,
            "chosen": chosen,
            "needs_your_pick": undecided,
            "downloaded": 0,
            "preview": True,
        }
        go = yes
        if chosen and not (preview or yes or term.json) and sys.stdin.isatty():
            for entry in chosen:
                upload = entry["source"]
                term.err.print(f"[bold]{escape(entry['label'])}[/]\n  {escape(upload['url'])}")
            go = typer.confirm("Rip these YouTube uploads as MP3?", default=False, err=True)
        if chosen and go:
            body = set_sources.download_body(name, request_id or source, chosen)
            # Apply the same input validation as the low-level download command.
            body = DownloadRequest.model_validate(body).model_dump(mode="json")
            followed = follow(term, local, local.request("POST", "/downloads", data=body)["result"])
            job = followed.job
            result.update(
                preview=False,
                job=job,
                downloaded=int((job.get("counts") or {}).get("succeeded") or 0),
            )
            if request_id and job["state"] == "completed":
                current = local.request("GET", f"/requests/{request_id}", params={"limit": 1})[
                    "result"
                ]
                checked = finish_checks(
                    local,
                    local.request(
                        "POST",
                        f"/requests/{request_id}/refresh",
                        data={"revision": current["revision"]},
                    ),
                )
                result["counts"] = checked["result"].get("counts")
        emit(envelope(result))
