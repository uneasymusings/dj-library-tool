"""Song recognition over a DJ set's audio, one short window at a time.

Shazam (through shazamio, no account, unofficial) is the default. AudD and ACRCloud are
licensed services configured with API keys. A recognition is evidence of what played,
not a tracklist: DJ mixes overlap, pitch-shift and edit the songs they play.
"""

import asyncio
import base64
import hashlib
import hmac
import inspect
import math
import os
import re
import time
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

import httpx

from djlib.audio.windows import duration, window_wav
from djlib.domain.errors import AppError

RECOGNIZERS = ("shazam", "audd", "acrcloud")
# Python 3.12: shazamio pins shazamio-core 1.1.2, which has no macOS or Windows wheels for 3.13.
INSTALL = (
    "Song recognition is not installed. Rerun the djlib installer, or run: "
    "uv tool install --force --python 3.12 "
    "'dj-library-tool[download,recognize] @ <release wheel URL>'"
)
# These would repeat for every window, so a scan stops instead of recording each one.
FATAL = frozenset(
    {
        "RECOGNITION_RATE_LIMITED",
        "RECOGNITION_UNAVAILABLE",
        "RECOGNIZER_NOT_CONFIGURED",
        "RECOGNIZER_REJECTED",
        "DEPENDENCY_REQUIRED",
    }
)
RETRY_WAITS = (3.0, 10.0)  # a window is asked again after a dropped connection or server error
OUTAGE_WINDOWS = 5  # windows in a row without any answer before a scan stops

OnProbe = Callable[["Probe", int, int], object]
ShouldStop = Callable[[], object]


@dataclass(frozen=True)
class Match:
    provider: str
    track_id: str
    artist: str
    title: str
    isrc: str | None = None
    song_offset: float | None = None  # seconds into the song where the window matched
    skew: float | None = None
    url: str | None = None

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Match":
        return cls(**{name: data.get(name) for name in cls.__dataclass_fields__})


@dataclass(frozen=True)
class Probe:
    at: float  # seconds into the set where the window starts
    window: float
    match: Match | None = None
    error: str | None = None

    def as_dict(self) -> dict:
        return {
            "at": self.at,
            "window": self.window,
            "match": self.match.as_dict() if self.match else None,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Probe":
        match = data.get("match")
        return cls(
            float(data["at"]),
            float(data["window"]),
            Match.from_dict(match) if isinstance(match, dict) else None,
            data.get("error"),
        )


class Recognizer(Protocol):
    """Names one song in a short WAV window.

    Whole-file services may also offer
    ``async scan_file(path, *, on_probe=None, should_stop=None) -> list[Probe]``,
    which ``scan`` uses instead of cutting windows itself.
    """

    name: str
    licensed: bool

    async def recognize(self, wav: bytes) -> Match | None: ...


class RateLimited(Exception):
    """The service asked for fewer requests."""


class Unreachable(Exception):
    """No answer: the connection failed or the service failed on its side (HTTP 5xx)."""


class Pace:
    """Spacing between a service's requests, and patience when it pushes back or drops out.

    On a rate limit, back-off starts at 10 s and doubles up to 5 minutes. It carries over from
    one window to the next and eases off after answers; after about 15 minutes of waiting
    without an answer the scan gives up rather than hammer the service. One address, no
    rotation. A dropped connection or server error is asked again shortly after; when several
    windows in a row get no answer at all, the service counts as unreachable.
    """

    def __init__(
        self,
        interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        first: float = 10.0,
        longest: float = 300.0,
        budget: float = 900.0,
    ):
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self.first, self.longest, self.budget = first, longest, budget
        self.delay = first
        self.waited = 0.0
        self.missed = 0
        self.ready_at: float | None = None

    async def call(
        self,
        request: Callable[[], Awaitable[dict]],
        service: str,
        *,
        retries: tuple[float, ...] = RETRY_WAITS,
    ) -> dict:
        waits = iter(retries)
        while True:
            now = self.clock()
            start = now if self.ready_at is None else max(now, self.ready_at)
            self.ready_at = start + self.interval
            if start > now:
                await self.sleep(start - now)
            try:
                result = await request()
            except RateLimited:
                if self.waited >= self.budget:
                    self.waited = 0.0  # a later scan gets its own quarter of an hour
                    raise AppError(
                        "RECOGNITION_RATE_LIMITED",
                        f"{service} is turning away requests from this computer for now; "
                        "try the scan again later.",
                        502,
                        True,
                    ) from None
                self.ready_at = max(self.ready_at, self.clock() + self.delay)
                self.waited += self.delay
                self.delay = min(self.delay * 2, self.longest)
                continue
            except Unreachable as exc:
                if (wait := next(waits, None)) is not None:
                    self.ready_at = max(self.ready_at, self.clock() + wait)
                    continue
                self.missed += 1
                if self.missed >= OUTAGE_WINDOWS:
                    self.missed = 0
                    raise AppError(
                        "RECOGNITION_UNAVAILABLE",
                        f"{service} could not be reached; check the internet connection and "
                        "run the scan again.",
                        502,
                        True,
                    ) from exc
                raise AppError(
                    "RECOGNITION_FAILED", f"{service} did not answer just now.", 502, True
                ) from exc
            self.waited, self.missed = 0.0, 0
            self.delay = max(self.first, self.delay / 2)
            return result


PACES: dict[str, Pace] = {}


def shared_pace(service: str, interval: float) -> Pace:
    """One pace per service in this process, so every scan and job keeps the same spacing."""
    pace = PACES.setdefault(service, Pace(interval))
    pace.interval = interval
    return pace


class ShazamRecognizer:
    """Shazam through shazamio: free and unofficial, so it is paced politely."""

    name = "shazam"
    licensed = False

    def __init__(
        self,
        *,
        environ: Mapping[str, str] = os.environ,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        pace: Pace | None = None,
    ):
        try:
            from shazamio import Shazam
        except ImportError as exc:  # also pydub without audioop on Python 3.13
            raise AppError("DEPENDENCY_REQUIRED", INSTALL) from exc
        self.shazam = Shazam(http_client=ShazamHttp(transport))
        interval = _seconds(environ.get("DJLIB_SHAZAM_INTERVAL"), 1.0)
        self.pace = pace or Pace(interval, clock=clock, sleep=sleep)

    async def recognize(self, wav: bytes) -> Match | None:
        async def request() -> dict:
            try:
                return await self.shazam.recognize(wav)
            except (RateLimited, Unreachable, AppError):
                raise
            except Exception as exc:  # shazamio's own errors are not part of its API
                if _rate_limited(exc):
                    raise RateLimited from exc
                raise AppError(
                    "RECOGNITION_FAILED",
                    "Shazam could not read this part of the set.",
                    502,
                    True,
                ) from exc

        return shazam_match(await self.pace.call(request, "Shazam"))


class ShazamHttp:
    """shazamio's requests without its built-in retries, so ``Pace`` decides when to retry."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        self.transport = transport

    async def request(self, method: str, url: str, *args, **kwargs):
        try:
            async with httpx.AsyncClient(transport=self.transport, timeout=30) as client:
                response = await client.request(
                    method,
                    url,
                    headers=kwargs.get("headers"),
                    params=kwargs.get("params"),
                    json=kwargs.get("json"),
                )
        except httpx.TransportError as exc:
            raise Unreachable from exc
        if response.status_code == 429:
            raise RateLimited
        if response.status_code >= 500:
            raise Unreachable
        response.raise_for_status()
        return response.json()


def shazam_match(reply: object) -> Match | None:
    track = reply.get("track") if isinstance(reply, dict) else None
    if not isinstance(track, dict) or not track.get("key"):
        return None
    matches = reply.get("matches")
    first = matches[0] if isinstance(matches, list) and matches else None
    first = first if isinstance(first, dict) else {}
    # DJs play songs faster, slower or pitched; a skew is reported, never filtered.
    skew = _number(first.get("timeskew"))
    if skew is None:
        skew = _number(first.get("frequencyskew"))
    return Match(
        "shazam",
        _text(track.get("key"), 100),
        _text(track.get("subtitle")),
        _text(track.get("title")),
        _text(track.get("isrc"), 20) or None,
        _number(first.get("offset")),
        skew,
        _link(track.get("url")),
    )


AUDD_API = "https://api.audd.io/"
AUDD_ENTERPRISE = "https://enterprise.audd.io/"
AUDD_CHUNK = 12.0  # AudD recognizes, and bills, whole files in 12-second chunks
AUDD_ACCOUNT = frozenset({900, 901, 902})  # invalid token, no token, limit reached


class AuddRecognizer:
    """AudD: windows through the standard API, whole sets through the enterprise endpoint."""

    name = "audd"
    licensed = True

    def __init__(
        self,
        token: str,
        *,
        stride: float = 24.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        pace: Pace | None = None,
    ):
        self.token = token
        self.skip = max(0, round(stride / AUDD_CHUNK) - 1)
        self.transport = transport
        self.pace = pace or Pace(0.0, clock=clock, sleep=sleep)

    async def recognize(self, wav: bytes) -> Match | None:
        async def request() -> dict:
            response = await _post(
                self.transport,
                AUDD_API,
                {"api_token": self.token, "return": "apple_music,spotify"},
                {"file": ("window.wav", wav, "audio/wav")},
                "AudD",
            )
            return _audd_reply(response)

        reply = await self.pace.call(request, "AudD")
        return audd_match(reply.get("result"))

    async def scan_file(
        self,
        path: Path,
        *,
        on_probe: OnProbe | None = None,
        should_stop: ShouldStop | None = None,
    ) -> list[Probe]:
        """One upload; AudD answers per scanned 12-second chunk."""
        if should_stop is not None and await _maybe(should_stop()):
            return []
        path = Path(path)
        total = await asyncio.to_thread(duration, path)

        async def request() -> dict:
            with path.open("rb") as stream:
                response = await _post(
                    self.transport,
                    AUDD_ENTERPRISE,
                    {
                        "api_token": self.token,
                        "skip": str(self.skip),
                        "every": "1",
                        "accurate_offsets": "true",
                    },
                    {"file": (path.name, stream)},
                    "AudD",
                    timeout=httpx.Timeout(60.0, read=1800.0),
                )
            return _audd_reply(response)

        # Re-sending a whole set after a dropped upload is left to the next scan.
        reply = await self.pace.call(request, "AudD", retries=())
        probes = audd_probes(reply.get("result"), total, self.skip)
        for done, probe in enumerate(probes, 1):
            if on_probe is not None:
                await _maybe(on_probe(probe, done, len(probes)))
        return probes


def _audd_reply(response: httpx.Response) -> dict:
    reply = _json(response, "AudD")
    if reply.get("status") == "error":
        error = reply.get("error") if isinstance(reply.get("error"), dict) else {}
        if error.get("error_code") in AUDD_ACCOUNT:
            raise AppError(
                "RECOGNIZER_REJECTED",
                "AudD refused the API token or its request limit is used up; "
                "check DJLIB_AUDD_TOKEN.",
                502,
            )
        raise AppError("RECOGNITION_FAILED", "AudD could not read this part of the set.", 502, True)
    return reply


def audd_match(song: object) -> Match | None:
    if not isinstance(song, dict):
        return None
    artist, title = _text(song.get("artist")), _text(song.get("title"))
    if not title:
        return None
    apple = song.get("apple_music") if isinstance(song.get("apple_music"), dict) else {}
    spotify = song.get("spotify") if isinstance(song.get("spotify"), dict) else {}
    ids = spotify.get("external_ids") if isinstance(spotify.get("external_ids"), dict) else {}
    isrc = _text(song.get("isrc"), 20) or _text(apple.get("isrc"), 20) or _text(ids.get("isrc"), 20)
    return Match(
        "audd",
        # The standard and enterprise endpoints share no song id; the credit is stable.
        f"{artist} - {title}".casefold(),
        artist,
        title,
        isrc or None,
        _clock(song.get("timecode")),
        None,
        _link(song.get("song_link")),
    )


def audd_probes(result: object, total: float, skip: int) -> list[Probe]:
    """Every scanned chunk, with or without a song (AudD only lists chunks it named)."""
    found: dict[float, Match | None] = {}
    for chunk in result if isinstance(result, list) else []:
        at = _clock(chunk.get("offset")) if isinstance(chunk, dict) else None
        if at is None:
            continue
        songs = [s for s in chunk.get("songs") or [] if isinstance(s, dict)]
        best = max(songs, key=lambda s: _number(s.get("score")) or 0.0, default=None)
        found[at] = audd_match(best) or found.get(at)
    step = AUDD_CHUNK * (skip + 1)
    count = int((total - AUDD_CHUNK) // step) + 1 if total >= AUDD_CHUNK else 0
    for k in range(count):
        found.setdefault(k * step, None)
    return [Probe(at, AUDD_CHUNK, match) for at, match in sorted(found.items())]


ACRCLOUD_NO_RESULT = frozenset({1001, 2004})  # nothing matched; no fingerprint (silence)
ACRCLOUD_ACCOUNT = frozenset({3001, 3002, 3003, 3014})  # key, request, quota, signature
ACRCLOUD_BUSY = 3015  # requests per second exceeded


class AcrCloudRecognizer:
    """ACRCloud's identification API, signed with the project's access secret."""

    name = "acrcloud"
    licensed = True

    def __init__(
        self,
        host: str,
        key: str,
        secret: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[object]] = asyncio.sleep,
        wall: Callable[[], float] = time.time,
        pace: Pace | None = None,
    ):
        self.host = host.strip().removeprefix("https://").rstrip("/")
        if not re.fullmatch(r"[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+", self.host):
            raise AppError(
                "RECOGNIZER_NOT_CONFIGURED",
                "Set DJLIB_ACRCLOUD_HOST to the project's host name, "
                "such as identify-eu-west-1.acrcloud.com.",
            )
        self.key, self.secret = key, secret
        self.transport, self.wall = transport, wall
        self.pace = pace or Pace(0.0, clock=clock, sleep=sleep)

    def signed(self, size: int) -> dict:
        timestamp = str(int(self.wall()))
        text = "\n".join(("POST", "/v1/identify", self.key, "audio", "1", timestamp))
        digest = hmac.new(self.secret.encode(), text.encode(), hashlib.sha1).digest()
        return {
            "access_key": self.key,
            "sample_bytes": str(size),
            "timestamp": timestamp,
            "signature": base64.b64encode(digest).decode("ascii"),
            "data_type": "audio",
            "signature_version": "1",
        }

    async def recognize(self, wav: bytes) -> Match | None:
        async def request() -> dict:
            response = await _post(
                self.transport,
                f"https://{self.host}/v1/identify",
                self.signed(len(wav)),
                {"sample": ("window.wav", wav, "audio/wav")},
                "ACRCloud",
            )
            reply = _json(response, "ACRCloud")
            status = reply.get("status") if isinstance(reply.get("status"), dict) else {}
            code = status.get("code")
            if code == ACRCLOUD_BUSY:
                raise RateLimited
            if code in ACRCLOUD_ACCOUNT:
                raise AppError(
                    "RECOGNIZER_REJECTED",
                    "ACRCloud refused the key, signature or quota; check DJLIB_ACRCLOUD_KEY "
                    "and DJLIB_ACRCLOUD_SECRET.",
                    502,
                )
            if code not in {0, *ACRCLOUD_NO_RESULT}:
                raise AppError(
                    "RECOGNITION_FAILED", "ACRCloud could not read this part of the set.", 502, True
                )
            return reply

        return acrcloud_match(await self.pace.call(request, "ACRCloud"))


def acrcloud_match(reply: object) -> Match | None:
    metadata = reply.get("metadata") if isinstance(reply, dict) else None
    music = metadata.get("music") if isinstance(metadata, dict) else None
    song = music[0] if isinstance(music, list) and music else None
    if not isinstance(song, dict) or not _text(song.get("title")):
        return None
    title = _text(song.get("title"))
    names = [a.get("name") for a in song.get("artists") or [] if isinstance(a, dict)]
    artist = _text(", ".join(name for name in names if isinstance(name, str)))
    ids = song.get("external_ids") if isinstance(song.get("external_ids"), dict) else {}
    isrc = ids.get("isrc")
    isrc = isrc[0] if isinstance(isrc, list) and isrc else isrc
    offset = _number(song.get("play_offset_ms"))
    return Match(
        "acrcloud",
        _text(song.get("acrid"), 100) or f"{artist} - {title}".casefold(),
        artist,
        title,
        _text(isrc, 20) or None,
        offset / 1000 if offset is not None else None,
        None,
        None,
    )


def recognizer(name: str | None = None, environ: Mapping[str, str] = os.environ) -> Recognizer:
    """The recognizer named, or DJLIB_RECOGNIZER, or Shazam.

    Recognizers of one service share their pacing and back-off within the process.
    """
    chosen = (name or environ.get("DJLIB_RECOGNIZER") or "shazam").strip().lower()
    if chosen == "shazam":
        interval = _seconds(environ.get("DJLIB_SHAZAM_INTERVAL"), 1.0)
        return ShazamRecognizer(environ=environ, pace=shared_pace("shazam", interval))
    if chosen == "audd":
        if not environ.get("DJLIB_AUDD_TOKEN"):
            raise AppError(
                "RECOGNIZER_NOT_CONFIGURED", "AudD needs an API token: set DJLIB_AUDD_TOKEN."
            )
        return AuddRecognizer(environ["DJLIB_AUDD_TOKEN"], pace=shared_pace("audd", 0.0))
    if chosen == "acrcloud":
        names = ("DJLIB_ACRCLOUD_HOST", "DJLIB_ACRCLOUD_KEY", "DJLIB_ACRCLOUD_SECRET")
        if missing := [n for n in names if not environ.get(n)]:
            raise AppError(
                "RECOGNIZER_NOT_CONFIGURED",
                f"ACRCloud needs a project's details: set {', '.join(missing)}.",
            )
        return AcrCloudRecognizer(*(environ[n] for n in names), pace=shared_pace("acrcloud", 0.0))
    raise AppError("INPUT_INVALID", "Choose shazam, audd or acrcloud for song recognition.")


def placements(
    total: float, *, window: float = 12.0, stride: float = 30.0, offset: float = 9.0
) -> list[float]:
    """Window starts at offset + k * stride that fit inside ``total`` seconds."""
    if not (0 < window <= 60 and stride > 0 and offset >= 0):
        raise AppError("INPUT_INVALID", "Use windows of up to 60 seconds and a positive stride.")
    if total < offset + window:
        return []
    count = int((total - window - offset) // stride) + 1
    return [round(offset + k * stride, 3) for k in range(count)]


async def scan(
    audio_path: Path,
    recognizer: Recognizer,
    *,
    window: float = 12.0,
    stride: float = 30.0,
    offset: float = 9.0,
    on_probe: OnProbe | None = None,
    should_stop: ShouldStop | None = None,
    skip: Collection[float] = (),
) -> list[Probe]:
    """Recognize the set window by window, in order; pacing lives in the recognizer.

    A window that fails is kept as ``Probe(error=...)`` and the scan goes on; errors that
    would repeat for every window (rate limits, refused keys, a service that stopped
    answering) end the scan. A recognizer with ``scan_file`` handles the whole file itself.
    Windows starting at a time in ``skip`` (heard before an interruption) are not asked
    again; progress counts them as done, and only new probes are returned.
    """
    if (scan_file := getattr(recognizer, "scan_file", None)) is not None:
        return await scan_file(Path(audio_path), on_probe=on_probe, should_stop=should_stop)
    total = await asyncio.to_thread(duration, Path(audio_path))
    starts = placements(total, window=window, stride=stride, offset=offset)
    heard = {round(at, 3) for at in skip}
    left = [start for start in starts if start not in heard]
    probes: list[Probe] = []
    for start in left:
        if should_stop is not None and await _maybe(should_stop()):
            break
        probes.append(await _probe(audio_path, recognizer, start, window))
        if on_probe is not None:
            done = len(starts) - len(left) + len(probes)
            await _maybe(on_probe(probes[-1], done, len(starts)))
    return probes


async def refine(
    audio_path: Path,
    recognizer: Recognizer,
    probes: list[Probe],
    *,
    window: float = 12.0,
    min_spacing: float = 10.0,
    limit: int | None = None,
    on_probe: OnProbe | None = None,
    should_stop: ShouldStop | None = None,
) -> list[Probe]:
    """Probe between neighbours that disagree, to place where one song gives way to the next.

    Midpoints go level by level, each at least ``min_spacing`` from its neighbours, until
    neighbours agree, ``limit`` new probes (default: as many as given) are spent or
    ``should_stop()`` is true. A failed window agrees with no neighbour. When the budget
    runs short, it is spread over the whole set. Returns every probe, sorted by position.
    """
    found = sorted(probes, key=lambda p: p.at)
    budget = len(probes) if limit is None else max(0, limit)
    done = 0
    while done < budget:
        gaps = [
            (a.at + b.at) / 2
            for a, b in zip(found, found[1:], strict=False)
            if not _agree(a, b) and (b.at - a.at) / 2 >= min_spacing
        ]
        if not gaps:
            break
        if len(gaps) > (left := budget - done):
            gaps = [gaps[(2 * k + 1) * len(gaps) // (2 * left)] for k in range(left)]
        planned = done + len(gaps)
        for at in gaps:
            if should_stop is not None and await _maybe(should_stop()):
                return sorted(found, key=lambda p: p.at)
            found.append(await _probe(audio_path, recognizer, round(at, 3), window))
            done += 1
            if on_probe is not None:
                await _maybe(on_probe(found[-1], done, planned))
        found.sort(key=lambda p: p.at)
    return found


async def _probe(audio_path: Path, recognizer: Recognizer, at: float, window: float) -> Probe:
    try:
        wav = await asyncio.to_thread(window_wav, Path(audio_path), at, window)
        return Probe(at, window, await recognizer.recognize(wav))
    except AppError as error:
        if error.code in FATAL:
            raise
        return Probe(at, window, None, error.message)


def _agree(a: Probe, b: Probe) -> bool:
    """The same song, or no song on both sides; a failed window is unknown."""
    return not (a.error or b.error) and _track(a) == _track(b)


def _track(probe: Probe) -> str | None:
    return probe.match.track_id if probe.match else None


async def _maybe(value: object) -> object:
    return await value if inspect.isawaitable(value) else value


async def _post(
    transport: httpx.AsyncBaseTransport | None,
    url: str,
    data: dict,
    files: dict,
    service: str,
    *,
    timeout: float | httpx.Timeout = 30.0,
) -> httpx.Response:
    try:
        async with httpx.AsyncClient(transport=transport, timeout=timeout) as client:
            response = await client.post(url, data=data, files=files)
    except httpx.TransportError as exc:
        raise Unreachable from exc
    except httpx.HTTPError as exc:
        raise AppError(
            "RECOGNITION_FAILED", f"{service} gave no usable answer just now.", 502, True
        ) from exc
    if response.status_code == 429:
        raise RateLimited
    if response.status_code >= 500:
        raise Unreachable
    return response


def _json(response: httpx.Response, service: str) -> dict:
    try:
        response.raise_for_status()
        reply = response.json()
        if not isinstance(reply, dict):
            raise ValueError("A JSON object is required.")
    except (httpx.HTTPError, ValueError) as exc:
        raise AppError(
            "RECOGNITION_FAILED", f"{service} gave no usable answer just now.", 502, True
        ) from exc
    return reply


def _rate_limited(error: Exception) -> bool:
    response = getattr(error, "response", None)
    statuses = {
        getattr(error, "status", None),
        getattr(error, "status_code", None),
        getattr(response, "status_code", None),
    }
    # Not a bare "429" in the text: request URLs carry random ids.
    text = f"{type(error).__name__} {error}".lower()
    compact = text.replace(" ", "").replace("_", "")
    return 429 in statuses or "too many requests" in text or "ratelimit" in compact


def _seconds(value: str | None, default: float) -> float:
    try:
        seconds = float(value) if value else default
    except ValueError:
        return default
    return seconds if math.isfinite(seconds) and seconds >= 0 else default


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value) if math.isfinite(value) else None


def _text(value: object, limit: int = 500) -> str:
    return str(value).strip()[:limit] if isinstance(value, str | int) else ""


def _link(value: object) -> str | None:
    return value[:2000] if isinstance(value, str) and value.startswith("https://") else None


def _clock(value: object) -> float | None:
    """Seconds from "SS", "MM:SS" or "H:MM:SS"."""
    if isinstance(value, int | float) and not isinstance(value, bool):
        return float(value)
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,6}(:\d{1,2}){0,2}", value.strip()):
        return None
    seconds = 0
    for part in value.strip().split(":"):
        seconds = seconds * 60 + int(part)
    return float(seconds)
