"""Label managed download derivatives while preserving acquisition bytes in incoming/."""

from pathlib import Path

from mutagen import MutagenError
from mutagen.flac import FLAC

from djlib.domain.contracts import TrackInput
from djlib.domain.errors import AppError


def tag_download_copy(path: Path, track: TrackInput) -> None:
    """Write chosen display labels to a staged FLAC copy; these are not identity evidence."""
    try:
        audio = FLAC(path)
        audio["artist"] = [track.artist]
        audio["title"] = [f"{track.title} ({track.version})" if track.version else track.title]
        audio.save()
    except (MutagenError, OSError, ValueError) as exc:
        raise AppError(
            "TAG_PREPARATION_FAILED", "Could not label the managed download copy."
        ) from exc
