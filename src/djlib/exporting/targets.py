"""Documented USB playback limits, not a claim that an export or player was tested.

An empty assessment means only that the supplied audio properties meet this
table. It does not inspect container headers, DRM, media, firmware, beatgrids,
native libraries, or a physical player. Unknown required properties are issues.
Manufacturer-authored manuals are primary sources even where a mirror is linked.
"""

import re
from copy import deepcopy

from djlib.domain.errors import AppError

_EXPORT_GUIDE = (
    "https://cdn.rekordbox.com/files/20260318114024/OneLibrary-Compatible-USB-Device-Export_en.pdf"
)
_PARTITION_NOTE = (
    "An exhaustive supported partition-scheme list is not established by these sources. "
    "Do not infer that a scheme is supported merely because it is not explicitly excluded."
)
_FIRST_PARTITION = (
    "With multiple partitions, the first is used unless another contains the rekordbox library."
)


def _audio_formats(lossless_rates: list[int], lossy_rates: list[int], *, alac: bool) -> dict:
    result = {
        "mp3": {
            "sample_rates_hz": lossy_rates,
            "bitrate_bps": [32_000, 320_000],
            "extensions": [".mp3"],
            "encoding": "MPEG-1 Audio Layer III; CBR or VBR",
        },
        "aac": {
            "sample_rates_hz": lossy_rates,
            "bitrate_bps": [16_000, 320_000],
            "extensions": [".m4a", ".aac", ".mp4"],
            "codec_profiles": ["LC"],
            "encoding": "MPEG-2/MPEG-4 AAC LC; CBR or VBR",
        },
        "pcm": {
            "sample_rates_hz": lossless_rates,
            "bit_depths": [16, 24],
            "extensions": [".wav", ".aif", ".aiff"],
            "encoding": "Uncompressed integer PCM in WAV or AIFF",
        },
        "flac": {
            "sample_rates_hz": lossless_rates,
            "bit_depths": [16, 24],
            "extensions": [".flac", ".fla"],
            "encoding": "FLAC",
        },
    }
    if alac:
        result["alac"] = {
            "sample_rates_hz": lossless_rates,
            "bit_depths": [16, 24],
            "extensions": [".m4a"],
            "encoding": "Apple Lossless",
        }
    return result


_LEGACY_AUDIO = _audio_formats([44_100, 48_000], [32_000, 44_100, 48_000], alac=False)
del _LEGACY_AUDIO["flac"]
_LEGACY_AUDIO["mp3"].update(
    {
        "sample_rates_hz": [16_000, 22_050, 24_000, 32_000, 44_100, 48_000],
        "bitrate_bps": [8_000, 320_000],
        "bitrate_by_sample_rate": [
            {"sample_rates_hz": [16_000, 22_050, 24_000], "bitrate_bps": [8_000, 160_000]},
            {"sample_rates_hz": [32_000, 44_100, 48_000], "bitrate_bps": [32_000, 320_000]},
        ],
        "encoding": "MPEG-1/MPEG-2 Audio Layer III; CBR or VBR",
    }
)
_LEGACY_AUDIO["aac"]["sample_rates_hz"] = [16_000, 22_050, 24_000, 32_000, 44_100, 48_000]

_PROFILES = {
    "cdj-2000nxs": {
        "name": "Pioneer CDJ-2000NXS",
        "library_format": "device_library",
        "filesystems": ["FAT16", "FAT32", "HFS+"],
        "unsupported_partition_schemes": [],
        "partition_notes": [_PARTITION_NOTE, _FIRST_PARTITION],
        "audio_formats": _LEGACY_AUDIO,
        "notes": ["This is CDJ-2000NXS, not CDJ-2000NXS2. FLAC and ALAC are unsupported."],
        "docs": [
            {
                "title": "Pioneer CDJ-2000NXS Operating Instructions, pages 6-7 (mirror)",
                "url": "https://www.manualslib.com/manual/712075/Pioneer-Cdj-2000nxs.html?page=6",
                "additional_url": "https://www.manualslib.com/manual/712075/Pioneer-Cdj-2000nxs.html?page=7",
                "publisher": "Pioneer",
            },
        ],
    },
    "cdj-3000": {
        "name": "Pioneer DJ CDJ-3000",
        "library_format": "device_library",
        "filesystems": ["FAT16", "FAT32", "exFAT", "HFS+"],
        "unsupported_partition_schemes": ["GPT"],
        "partition_notes": [_PARTITION_NOTE, "GUID Partition Map is explicitly unsupported."],
        "audio_formats": _audio_formats(
            [44_100, 48_000, 88_200, 96_000], [44_100, 48_000], alac=True
        ),
        "notes": [
            "Current FAQ includes exFAT; the older manual does not. Verify installed firmware.",
            "Case-sensitive HFS+ may not be recognized.",
            "Firmware 3.30 was withdrawn. The January 2026 notice restores Device Library; "
            "verify firmware on the actual player, especially if it still runs 3.30.",
        ],
        "docs": [
            {
                "title": "Pioneer DJ CDJ-3000 Instruction Manual, pages 11-13 (mirror)",
                "url": "https://manuals.plus/m/6f84c43b6ff14de6c4f18b2bef6febf9bd3bc51ac1b9c86c32ec930bbfff58fb_optim.pdf",
                "publisher": "Pioneer DJ",
            },
            {
                "title": "AlphaTheta CDJ-3000 USB storage FAQ",
                "url": "https://support.alphatheta.com/en-us/articles/4406135001625",
            },
            {
                "title": "CDJ-3000 firmware 3.30 important notice, January 7 2026",
                "url": "https://www.pioneerdj.com/en/news/2026/cdj-3000-firmware-ver330-important-notice/",
            },
        ],
    },
    "xdj-rx3": {
        "name": "Pioneer DJ XDJ-RX3",
        "library_format": "device_library",
        "filesystems": ["FAT16", "FAT32", "exFAT", "HFS+"],
        "unsupported_partition_schemes": ["GPT"],
        "partition_notes": [
            _PARTITION_NOTE,
            _FIRST_PARTITION,
            "GUID Partition Map is explicitly unsupported.",
        ],
        "audio_formats": _audio_formats([44_100, 48_000], [32_000, 44_100, 48_000], alac=False),
        "notes": [
            "USB/Export mode limits differ from software Performance mode limits.",
            "ALAC is not listed among supported USB audio formats.",
            "Current USB FAQ includes exFAT. Verify installed firmware.",
            "Case-sensitive HFS+ may not be recognized.",
        ],
        "docs": [
            {
                "title": "Pioneer DJ XDJ-RX3 Instruction Manual, pages 9-10 (mirror)",
                "url": "https://www.pioneer-dj.de/wp-content/uploads/2024/02/Pioneer-DJ-XDJ-RX3-User-Manual.pdf",
                "publisher": "Pioneer DJ",
            },
            {
                "title": "AlphaTheta XDJ-RX3 USB storage FAQ",
                "url": "https://support.alphatheta.com/en-us/articles/4409195984281",
            },
            {
                "title": "AlphaTheta XDJ-RX3 supported file formats FAQ",
                "url": "https://support.alphatheta.com/en-us/articles/4409211977881",
            },
        ],
    },
    "opus-quad": {
        "name": "Pioneer DJ OPUS-QUAD",
        "library_format": "onelibrary",
        "filesystems": ["FAT16", "FAT32", "exFAT", "HFS+"],
        "unsupported_partition_schemes": [],
        "partition_notes": [_PARTITION_NOTE, _FIRST_PARTITION],
        "audio_formats": _audio_formats(
            [44_100, 48_000, 88_200, 96_000], [44_100, 48_000], alac=True
        ),
        "notes": ["OneLibrary is the current name for Device Library Plus."],
        "docs": [
            {
                "title": "Pioneer DJ OPUS-QUAD Instruction Manual, pages 9-12",
                "url": "https://assets.pioneerdjhub.com/OPUS_QUAD_DRI1795D_manual_EN.pdf",
            },
            {
                "title": "AlphaTheta OneLibrary compatibility",
                "url": "https://alphatheta.com/en/onelibrary/",
            },
        ],
    },
}


def target_profiles() -> dict:
    """Return independent, JSON-compatible profiles; null partition schemes means unknown."""
    profiles = deepcopy(_PROFILES)
    for profile_id, profile in profiles.items():
        profile.update(
            {
                "id": profile_id,
                "researched_at": "2026-10-04",
                "assessment_scope": "documented_audio_properties_only",
                "firmware_verified": False,
                "hardware_verified": False,
                "partition_schemes": None,
                "partition_scheme_verification_required": True,
                "assessed_channels": [1, 2],
                "channel_assessment_note": (
                    "The engine checks only mono/stereo as a conservative subset; "
                    "this is not an exhaustive manufacturer channel-layout claim."
                ),
            }
        )
        profile["docs"].append({"title": "rekordbox USB export guide", "url": _EXPORT_GUIDE})
    return profiles


def _positive_int(value: object) -> int | None:
    # Do not coerce fractions or booleans into seemingly valid technical properties.
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal() and int(value) > 0:
        return int(value)
    return None


def assess_track(
    profile_id: str, properties: dict, *, file_extension: str | None = None
) -> list[str]:
    """Report unsupported or unknown required audio properties, without changing audio.

    Properties follow Inspection.as_dict(). Optional codec_profile is ffprobe's
    profile (AAC must be LC). Bit depth is required only for PCM/FLAC/ALAC; a
    lossy decoder's sample format does not describe the encoded file's depth.
    Only mono/stereo is assessed. When supplied, file_extension must be a
    supported suffix for the codec; this is not a container-header inspection.
    """
    if profile_id not in _PROFILES:
        raise AppError("TARGET_UNKNOWN", f"Unknown hardware target profile: {profile_id}")
    codec = str(properties.get("codec") or "").strip().lower()
    if not codec:
        return ["TARGET_CODEC_UNKNOWN"]
    # Only integer PCM accepted: pcm_f32le and other float variants must not pass.
    pcm_match = re.fullmatch(r"pcm_(?:s)?(\d+)(?:le|be)?", codec)
    family = "pcm" if pcm_match else codec
    spec = _PROFILES[profile_id]["audio_formats"].get(family)
    if spec is None:
        return ["TARGET_CODEC_UNSUPPORTED"]
    issues = []
    channels = _positive_int(properties.get("channels"))
    if channels is None:
        issues.append("TARGET_CHANNELS_UNKNOWN")
    elif channels not in {1, 2}:
        issues.append("TARGET_CHANNELS_UNSUPPORTED")
    if file_extension is not None:
        extension = file_extension.strip().lower()
        if not extension:
            issues.append("TARGET_EXTENSION_UNKNOWN")
        elif extension not in spec["extensions"]:
            issues.append("TARGET_EXTENSION_UNSUPPORTED")
    rate = _positive_int(properties.get("sample_rate"))
    if rate is None:
        issues.append("TARGET_SAMPLE_RATE_UNKNOWN")
    elif rate not in spec["sample_rates_hz"]:
        issues.append("TARGET_SAMPLE_RATE_UNSUPPORTED")
    if "bit_depths" in spec:
        depth = _positive_int(properties.get("bit_depth"))
        if depth is None:
            issues.append("TARGET_BIT_DEPTH_UNKNOWN")
        elif depth not in spec["bit_depths"]:
            issues.append("TARGET_BIT_DEPTH_UNSUPPORTED")
        if pcm_match:
            encoded_depth = int(pcm_match.group(1))
            if encoded_depth not in spec["bit_depths"]:
                if "TARGET_BIT_DEPTH_UNSUPPORTED" not in issues:
                    issues.append("TARGET_BIT_DEPTH_UNSUPPORTED")
            elif depth in spec["bit_depths"] and depth != encoded_depth:
                issues.append("TARGET_BIT_DEPTH_CONFLICT")
    if "bitrate_bps" in spec:
        bitrate = _positive_int(properties.get("bitrate_bps"))
        limits = spec["bitrate_bps"]
        for variant in spec.get("bitrate_by_sample_rate", []):
            if rate in variant["sample_rates_hz"]:
                limits = variant["bitrate_bps"]
                break
        if bitrate is None:
            issues.append("TARGET_BITRATE_UNKNOWN")
        elif not limits[0] <= bitrate <= limits[1]:
            issues.append("TARGET_BITRATE_UNSUPPORTED")
    if family == "aac":
        codec_profile = str(properties.get("codec_profile") or "").strip().upper()
        if not codec_profile:
            issues.append("TARGET_CODEC_PROFILE_UNKNOWN")
        elif codec_profile != "LC":
            issues.append("TARGET_CODEC_PROFILE_UNSUPPORTED")
    return issues
