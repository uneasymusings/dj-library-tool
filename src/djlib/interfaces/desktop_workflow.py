"""Bounded native actions from the authenticated local page, through the existing CLI."""

import asyncio
import contextlib
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from filelock import FileLock, Timeout
from pydantic import Field

from djlib.application.catalog_paging import attach_dj
from djlib.domain.errors import AppError
from djlib.domain.request_contracts import RequestContract
from djlib.interfaces.envelope import envelope
from djlib.workspace import atomic_json


class DesktopAction(RequestContract):
    action: Literal["import", "analysis", "usb"]
    idempotency_key: str = Field(min_length=1, max_length=200)


def collection_fingerprint(collection):
    rows = [(t.get("asset_revision_id"), t.get("sha256")) for t in collection["tracks"]]
    return hashlib.sha256(
        json.dumps([collection["name"], collection["revision"], rows]).encode()
    ).hexdigest()


def read_action(workspace, collection_id):
    try:
        value = json.loads(
            (workspace.runtime / "desktop-actions" / f"{collection_id}.json").read_text(
                encoding="utf-8"
            )
        )
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def workflow_status(application, collection_id):
    collection = application.collection(collection_id)
    tracks = attach_dj(application, collection["tracks"])
    present = analyzed = 0
    for track in tracks:
        try:
            path = Path(track["path"])
            stat = path.stat()
            present += path.is_file() and not bool(getattr(stat, "st_flags", 0) & 0x40000000)
        except (KeyError, TypeError, OSError):
            pass
        dj = track.get("dj") or {}
        analyzed += bool(dj.get("bpm") and dj.get("key"))
    action = read_action(application.workspace, collection_id)
    current = bool(action and action.get("fingerprint") == collection_fingerprint(collection))
    complete = current and action.get("state") == "completed"
    imported = bool(current and action.get("import_evidence")) or bool(
        complete and action.get("action") in {"import", "usb"}
    )
    result = (action or {}).get("usb_evidence") or (
        (action or {}).get("result") if complete and action.get("action") == "usb" else {}
    )
    if not isinstance(result, dict):
        result = {}
    usb = (
        current
        and result.get("playlist_on_device") is True
        and result.get("in_order") is True
        and result.get("found") == len(tracks) == result.get("expected")
    )
    return {
        "track_count": len(tracks),
        "local_files": present,
        "with_bpm_and_key": analyzed,
        "import_recorded": imported,
        "usb_verified_at_last_check": bool(usb),
        "usb_checked_at": result.get("checked_at") if usb else None,
        "usb_device": result.get("device") if usb else None,
        "hardware_playback_verified": False,
        "desktop_supported": sys.platform == "darwin",
        "action": action if current else None,
        "previous_action_is_stale": bool(action and not current),
    }


class DesktopRunner:
    def __init__(self, application):
        self.application = application
        self.task = None
        self.process = None
        self.active = None
        self.lock = None
        folder = application.workspace.runtime / "desktop-actions"
        for path in folder.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                if not isinstance(record, dict):
                    continue
                if record.get("state") == "running":
                    record.update(
                        state="interrupted",
                        error={
                            "message": "djlib restarted during this action. Check rekordbox before "
                            "trying again; the USB export may still be running."
                        },
                    )
                    atomic_json(path, record)
            except (OSError, ValueError):
                pass

    async def start(self, collection_id, body):
        collection = self.application.collection(collection_id)
        if not collection["tracks"]:
            raise AppError("INPUT_INVALID", "Build a crate with at least one owned track first.")
        if sys.platform != "darwin":
            raise AppError(
                "APP_AUTOMATION_UNSUPPORTED",
                "rekordbox export controls need macOS. Use the playlist files on this computer.",
            )
        fingerprint = collection_fingerprint(collection)
        old = read_action(self.application.workspace, collection_id)
        if old and old.get("idempotency_key") == body.idempotency_key:
            if old.get("action") != body.action or old.get("fingerprint") != fingerprint:
                raise AppError(
                    "IDEMPOTENCY_CONFLICT",
                    "This action changed. Refresh the crate before trying again.",
                )
            return old
        # One native action at a time, including another djlib workspace on this computer.
        lock = FileLock(str(Path(os.environ.get("TMPDIR", "/tmp")) / "djlib-desktop-action.lock"))
        try:
            lock.acquire(timeout=0)
        except Timeout as exc:
            raise AppError(
                "APP_BUSY",
                "A rekordbox action is already running. Finish it before starting another.",
            ) from exc
        record = {
            "action": body.action,
            "idempotency_key": body.idempotency_key,
            "fingerprint": fingerprint,
            "state": "running",
            "event": {
                "instruction": "Preparing the crate. Keep rekordbox open and your Mac unlocked."
            },
            "result": None,
            "error": None,
            "import_evidence": old.get("import_evidence")
            if old and old.get("fingerprint") == fingerprint
            else None,
            "usb_evidence": old.get("usb_evidence")
            if body.action != "usb" and old and old.get("fingerprint") == fingerprint
            else None,
        }
        path = self.application.workspace.runtime / "desktop-actions" / f"{collection_id}.json"
        try:
            atomic_json(path, record)
        except OSError:
            lock.release()
            raise
        self.active = (path, record)
        self.lock = lock
        self.task = asyncio.create_task(self.run(collection_id, path, record, lock))
        return record.copy()

    async def run(self, collection_id, path, record, lock):
        try:
            args = [
                sys.executable,
                "-P",
                "-m",
                "djlib.interfaces.cli",
                "--workspace",
                str(self.application.workspace.root),
                "--json",
                "rekordbox",
                {"import": "push", "analysis": "pull", "usb": "usb"}[record["action"]],
            ]
            if record["action"] != "analysis":
                args.append(collection_id)
            self.process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=2_000_000,
            )

            async def events():
                async for line in self.process.stderr:
                    try:
                        value = json.loads(line)
                        if isinstance(value, dict) and "event" in value:
                            record["event"] = value
                            atomic_json(path, record)
                    except ValueError:
                        pass

            async def output():
                data = bytearray()
                while chunk := await self.process.stdout.read(64 * 1024):
                    data.extend(chunk)
                    if len(data) > 2_000_000:
                        raise AppError(
                            "APP_ACTION_FAILED",
                            "The export returned too much output. Check the crate in rekordbox.",
                        )
                return bytes(data)

            data, _, code = await asyncio.wait_for(
                asyncio.gather(output(), events(), self.process.wait()), timeout=3600
            )
            reply = json.loads(data)
            if not isinstance(reply, dict):
                raise ValueError("Invalid export reply")
            if reply.get("ok") and not isinstance(reply.get("result"), dict):
                raise ValueError("Missing export result")
            if code != 0 or not reply.get("ok"):
                record.update(
                    state="failed",
                    error=reply.get("error")
                    or {"message": "The USB check did not pass. Review the export in rekordbox."},
                    result=reply.get("result"),
                )
            else:
                record.update(state="completed", result=reply.get("result"), event=None)
                if record["action"] in {"import", "usb"}:
                    record["import_evidence"] = {"checked_at": datetime.now(UTC).isoformat()}
                if record["action"] == "usb":
                    record["usb_evidence"] = {
                        **reply["result"],
                        "checked_at": datetime.now(UTC).isoformat(),
                    }
        except asyncio.CancelledError:
            record.update(
                state="interrupted",
                error={
                    "message": "djlib stopped during the action. Check rekordbox before trying "
                    "again; its USB export may still be running."
                },
            )
            raise
        except Exception as exc:
            record.update(
                state="failed",
                error={
                    "message": exc.message
                    if isinstance(exc, AppError)
                    else "The action could not finish. Check rekordbox and try again."
                },
            )
        finally:
            try:
                if self.process and self.process.returncode is None:
                    with contextlib.suppress(ProcessLookupError):
                        self.process.terminate()
                    await self.process.wait()
                record["finished_at"] = datetime.now(UTC).isoformat()
                atomic_json(path, record)
            finally:
                lock.release()
                self.active = None
                self.lock = None

    async def close(self):
        if self.task and not self.task.done():
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task
        # A task cancelled before its first turn never reaches run()'s finally block.
        if self.active:
            path, record = self.active
            try:
                record.update(
                    state="interrupted",
                    error={
                        "message": "djlib stopped before the native action finished. "
                        "Check rekordbox before retrying."
                    },
                )
                atomic_json(path, record)
            finally:
                if self.lock:
                    self.lock.release()
                self.active = self.lock = None


def register_http(app, application):
    runner = DesktopRunner(application)
    app.state.desktop_runner = runner

    @app.get("/collections/{collection_id}/workflow")
    async def workflow(collection_id: str):
        return envelope(await asyncio.to_thread(workflow_status, application, collection_id))

    @app.post("/collections/{collection_id}/desktop")
    async def desktop(collection_id: str, body: DesktopAction):
        return envelope(await runner.start(collection_id, body))
