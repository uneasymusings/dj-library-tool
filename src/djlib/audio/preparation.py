"""Label managed download derivatives while preserving acquisition bytes in incoming/."""

from pathlib import Path
from urllib.parse import urlsplit

from mutagen import MutagenError
from mutagen.flac import FLAC

from djlib.domain.contracts import TrackInput
from djlib.domain.errors import AppError

# Hosts a download can come from (djlib download, set --fetch), as a DJ would name them.
SITES = {"youtube.com": "YouTube", "youtu.be": "YouTube", "soundcloud.com": "SoundCloud"}


def rip_comment(source_url: str) -> str:
    """The comment rekordbox shows for a downloaded copy: a stream-quality rip, and from where.

    Downloads are re-encoded, so rekordbox reports a healthy bitrate; this says to buy it first.
    """
    host = (urlsplit(source_url).hostname or "").lower().removeprefix("www.")
    host = host.removeprefix("m.").removeprefix("music.")
    site = SITES.get(host) or ("Bandcamp" if host.endswith("bandcamp.com") else host or "the web")
    return f"Web rip from {site} (stream quality) — buy before playing out · {source_url}"


def tag_download_copy(
    path: Path, track: TrackInput, suffix: str = ".flac", source_url: str | None = None
) -> None:
    """Write chosen display labels to a staged FLAC or MP3 copy; these are not identity evidence.

    With ``source_url``, the comment says it is a web rip (rekordbox's Comments column).
    """
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import COMM, ID3, ID3NoHeaderError

    title = f"{track.title} ({track.version})" if track.version else track.title
    comment = rip_comment(source_url) if source_url else None
    try:
        if suffix == ".mp3":
            try:
                audio = EasyID3(path)
            except ID3NoHeaderError:
                audio = EasyID3()
            audio["artist"] = [track.artist]
            audio["title"] = [title]
            audio.save(path)
            if comment:
                tags = ID3(path)
                # Replaces any comment the site's metadata left, so this is the one shown.
                tags.setall("COMM", [COMM(encoding=3, lang="eng", desc="", text=[comment])])
                tags.save(path)
            return
        audio = FLAC(path)
        audio["artist"] = [track.artist]
        audio["title"] = [title]
        if comment:
            audio["comment"] = [comment]
        audio.save()
    except (MutagenError, OSError, ValueError) as exc:
        raise AppError(
            "TAG_PREPARATION_FAILED", "Could not label the managed download copy."
        ) from exc
