"""Separate, reproducible app working copies; no original or native database writes."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import mutagen
from mutagen.flac import FLAC
from mutagen.id3 import COMM, ID3, TBPM, TCON, TIT2, TKEY, TPE1
from mutagen.mp4 import MP4, MP4FreeForm

from djlib.audio.inspection import checksum, inspect_audio
from djlib.domain.errors import AppError


def pcm_hash(path: Path) -> str:
    """A native tag edit may change file bytes, but must not change decoded audio."""
    try:
        reply = subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-xerror",
                "-protocol_whitelist",
                "file,pipe",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-c:a",
                "pcm_s32le",
                "-f",
                "hash",
                "-hash",
                "sha256",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        value = reply.stdout.strip().removeprefix("SHA256=")
        if not re.fullmatch(r"[0-9a-f]{64}", value):
            raise ValueError
        return value
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise AppError(
            "AUDIO_VERIFY_FAILED", "FFmpeg could not verify the working-copy audio."
        ) from exc


def safe_name(value: str) -> str:
    # Keep Unicode in tags; simple short filenames work on older device filesystems.
    return re.sub(r"[^A-Za-z0-9._ -]+", "_", value).strip(" .")[:70] or "Track"


def _tag(path: Path, track: dict) -> dict:
    try:
        media = mutagen.File(path)
        if media is None:
            raise ValueError
        if media.tags is None:
            media.add_tags()
        title = track["title"] + (f" ({track['version']})" if track["version"] else "")
        annotations = track.get("dj_metadata", {}).get("annotations", {})
        bpm = annotations.get("bpm")
        key = annotations.get("key")
        genres = annotations.get("genres")
        notes = annotations.get("notes") or ""
        labels = []
        if annotations.get("tags"):
            labels.append("tags: " + ", ".join(annotations["tags"]))
        if annotations.get("set_role"):
            labels.append("role: " + annotations["set_role"])
        if annotations.get("energy") is not None:
            labels.append(f"energy: {annotations['energy']}/10")
        if labels:
            notes += ("\n" if notes else "") + "[DJ Library Tool] " + "; ".join(labels)
        written, warnings = ["artist", "title"], []
        if isinstance(media.tags, ID3):
            media.tags.add(TIT2(encoding=3, text=title))
            media.tags.add(TPE1(encoding=3, text=track["artist"]))
            if bpm:
                media.tags.add(TBPM(encoding=3, text=format(bpm["value"], "g")))
            if key:
                media.tags.add(TKEY(encoding=3, text=key["value"]))
            if genres is not None:
                media.tags.delall("TCON")
                if genres:
                    media.tags.add(TCON(encoding=3, text=genres))
            if notes:
                media.tags.add(COMM(encoding=3, lang="eng", desc="DJ Library Tool", text=notes))
        elif isinstance(media, MP4):
            media["\u00a9nam"] = [title]
            media["\u00a9ART"] = [track["artist"]]
            if bpm:
                value = bpm["value"]
                media["----:com.apple.iTunes:BPM"] = [
                    MP4FreeForm(format(value, "g").encode("utf-8"))
                ]
                if float(value).is_integer():
                    media["tmpo"] = [int(value)]
                else:
                    media.pop("tmpo", None)
                    warnings.append(
                        "MP4 integer tempo cannot represent fractional BPM; exact value is "
                        "in the manifest/freeform tag. Confirm it in the native app."
                    )
            if key:
                media["----:com.apple.iTunes:initialkey"] = [
                    MP4FreeForm(key["value"].encode("utf-8"))
                ]
            if genres is not None:
                media.pop("\u00a9gen", None)
                if genres:
                    media["\u00a9gen"] = genres
            if notes:
                media["\u00a9cmt"] = [notes]
        elif isinstance(media, FLAC):
            media["title"] = [title]
            media["artist"] = [track["artist"]]
            if bpm:
                media["bpm"] = [format(bpm["value"], "g")]
            if key:
                media["initialkey"] = [key["value"]]
            if genres is not None:
                media.pop("genre", None)
                if genres:
                    media["genre"] = genres
            if notes:
                media["comment"] = [notes]
        else:
            raise ValueError("Working-copy tag format is outside the supported subset.")
        written.extend(
            name
            for name, value in (("bpm", bpm), ("key", key), ("genres", genres), ("comments", notes))
            if value or (name == "genres" and value is not None)
        )
        media.save()
        return {
            "written_fields": written,
            "warnings": warnings,
            "native_analysis_performed": False,
            "original_modified": False,
        }
    except (OSError, ValueError, KeyError, TypeError, mutagen.MutagenError) as exc:
        raise AppError(
            "TAG_PREPARATION_FAILED", "Could not label the separate app working copy."
        ) from exc


def prepare_media(source: Path, track: dict, directory: Path, mode: str) -> dict:
    """Decode/hash each result before publishing it; interrupted files never become ready."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise AppError("DEPENDENCY_REQUIRED", "Delivery preparation requires FFmpeg and ffprobe.")
    original = inspect_audio(source)
    if original.sha256 != track["sha256"]:
        raise AppError(
            "RECONCILIATION_REQUIRED", "Catalog source bytes changed before preparation."
        )
    reuse_format = mode == "preserve" or (
        mode == "mp3_320"
        and original.codec == "mp3"
        and original.sample_rate in {44100, 48000}
        and original.channels <= 2
    )
    extension = source.suffix.lower() if reuse_format else (".mp3" if mode == "mp3_320" else ".wav")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (
        safe_name(f"{track['artist']} - {track['title']}") + "-" + track["sha256"][:16] + extension
    )
    # Deterministic private output supports a crash between promotion and catalog commit.
    if destination.exists():
        raise AppError(
            "WORKING_COPY_EXISTS", "An uncommitted working copy exists; inspect it before retrying."
        )
    reserve = max(original.size_bytes * 2, int(original.duration_seconds * 176400)) + 64 * 1024**2
    if shutil.disk_usage(directory).free < reserve:
        raise AppError("DISK_SPACE", "Insufficient space for isolated native-app working copies.")
    fd, temporary = tempfile.mkstemp(prefix=".prepare-", suffix=extension, dir=directory)
    os.close(fd)
    temporary = Path(temporary)
    try:
        if reuse_format:
            shutil.copyfile(source, temporary)
            if checksum(temporary) != original.sha256:
                raise AppError("FILE_CHANGED", "Source changed while creating the working copy.")
        else:
            encoding = (
                ["-c:a", "libmp3lame", "-b:a", "320k", "-ar", "44100", "-ac", "2"]
                if mode == "mp3_320"
                else ["-c:a", "pcm_s16le", "-ar", "44100", "-ac", "2"]
            )
            try:
                subprocess.run(
                    [
                        "ffmpeg",
                        "-nostdin",
                        "-v",
                        "error",
                        "-xerror",
                        "-y",
                        "-protocol_whitelist",
                        "file,pipe",
                        "-i",
                        str(source),
                        "-map",
                        "0:a:0",
                        *encoding,
                        str(temporary),
                    ],
                    capture_output=True,
                    timeout=180,
                    check=True,
                )
            except (OSError, subprocess.SubprocessError) as exc:
                raise AppError(
                    "CONVERSION_FAILED", "Could not prepare the requested compatibility copy."
                ) from exc
        metadata_handoff = _tag(temporary, track)
        inspected = inspect_audio(temporary)
        if abs(inspected.duration_seconds - original.duration_seconds) > 0.25:
            raise AppError("DURATION_CHANGED", "Prepared audio duration differs from the source.")
        if checksum(source) != original.sha256:
            raise AppError("FILE_CHANGED", "Source changed during preparation.")
        audio_hash = pcm_hash(temporary)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        # No existing user file can be replaced: output is inside a new per-item directory.
        os.link(temporary, destination)
        return {
            **track,
            "source_path": str(source),
            "source_sha256": original.sha256,
            "path": str(destination),
            "sha256": inspected.sha256,
            "pcm_sha256": audio_hash,
            "properties": inspected.as_dict(),
            "conversion": "working_copy" if reuse_format else mode,
            "metadata_handoff": metadata_handoff,
            "quality_note": (
                "Conversion does not improve source fidelity; supplied tags are display labels."
            ),
        }
    finally:
        temporary.unlink(missing_ok=True)
