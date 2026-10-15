"""Smoke-check song recognition where it is installed (the recognize extra, Python 3.12).

shazamio and its signing library (shazamio-core) import, djlib sees them as doctor does, and
djlib's Shazam recognizer makes a signature from a few seconds of generated tones. Nothing is
sent to Shazam.
"""

import asyncio
import io
import json
import math
import struct
import warnings
import wave
from importlib.metadata import version

RATE = 16000


def tones(seconds: int = 12) -> bytes:
    """A mono 16 kHz WAV of three alternating tones, like one window of a set."""
    samples = (
        int(8000 * math.sin(2 * math.pi * (440 + 220 * (n // RATE % 3)) * n / RATE))
        for n in range(RATE * seconds)
    )
    out = io.BytesIO()
    with wave.open(out, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(RATE)
        audio.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
    return out.getvalue()


async def check() -> None:
    with warnings.catch_warnings():
        # pydub, which shazamio uses, has invalid escape sequences in its regexes.
        warnings.simplefilter("ignore")
        import shazamio  # noqa: F401
        import shazamio_core  # noqa: F401
        from shazamio import Shazam  # noqa: F401  (what djlib's recognizer imports)

    from djlib.application.identification import shazam_installed
    from djlib.sources.recognition import ShazamRecognizer

    assert shazam_installed(), "djlib doesn't see song recognition as installed"
    recognizer = ShazamRecognizer(environ={})
    signature = await recognizer.shazam.core_recognizer.recognize_bytes(value=tones(), options=None)
    assert signature.signature.uri.startswith("data:audio/vnd.shazam.sig;base64,"), signature
    print(
        json.dumps(
            {
                "ok": True,
                "shazamio": version("shazamio"),
                "shazamio_core": version("shazamio-core"),
                "signature_samples": signature.signature.samples,
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(check())
