"""Label managed download derivatives while preserving acquisition bytes in incoming/."""

from pathlib import Path

from mutagen import MutagenError
from mutagen.flac import FLAC

from djlib.domain.contracts import TrackInput
from djlib.domain.errors import AppError


def tag_download_copy(path: Path, track: TrackInput, suffix: str = ".flac") -> None:
    """Write chosen display labels to a staged FLAC or MP3 copy; these are not identity evidence."""
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError

    title = f"{track.title} ({track.version})" if track.version else track.title
    try:
        if suffix == ".mp3":
            try:
                audio = EasyID3(path)
            except ID3NoHeaderError:
                audio = EasyID3()
            audio["artist"] = [track.artist]
            audio["title"] = [title]
            audio.save(path)
            return
        audio = FLAC(path)
        audio["artist"] = [track.artist]
        audio["title"] = [title]
        audio.save()
    except (MutagenError, OSError, ValueError) as exc:
        raise AppError(
            "TAG_PREPARATION_FAILED", "Could not label the managed download copy."
        ) from exc
