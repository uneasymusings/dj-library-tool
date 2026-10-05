"""Native-app input subsets, separate from standalone player/USB capabilities.

These checks never operate an app or prove import, analysis, or musical accuracy.
Serato documents format families but not exhaustive numeric limits in the cited
DJ Pro pages. Its numeric assessment bounds below are conservative engine policy,
not purported vendor maxima. Serato Sample specifications are not used.
"""

import re
from copy import deepcopy

from djlib.domain.errors import AppError

_REKORDBOX_MANUAL = "https://cdn.rekordbox.com/files/20260807093645/rekordbox7.2.18_manual_EN.pdf"
_SERATO_FORMATS = "https://support.serato.com/hc/en-us/articles/228119608-Supported-File-Types"


def _formats(lossless_rates: list[int], lossy_rates: list[int]) -> dict:
    return {
        "pcm": {
            "extensions": [".wav", ".aif", ".aiff"],
            "sample_rates_hz": lossless_rates,
            "bit_depths": [16, 24],
            "encoding": "Uncompressed integer PCM",
        },
        "flac": {
            "extensions": [".flac"],
            "sample_rates_hz": lossless_rates,
            "bit_depths": [16, 24],
        },
        "alac": {
            "extensions": [".m4a"],
            "sample_rates_hz": lossless_rates,
            "bit_depths": [16, 24],
        },
        "mp3": {
            "extensions": [".mp3"],
            "sample_rates_hz": lossy_rates,
            "bitrate_bps": [32_000, 320_000],
            "encoding": "MPEG-1 Audio Layer III; CBR or VBR",
        },
        "aac": {
            # Raw .aac is macOS-only in Serato; keep the engine's cross-platform subset.
            "extensions": [".m4a"],
            "sample_rates_hz": lossy_rates,
            "bitrate_bps": [16_000, 320_000],
            "codec_profiles": ["LC"],
        },
    }


_RB_FORMATS = _formats(
    [44_100, 48_000, 88_200, 96_000, 192_000],
    [16_000, 22_050, 24_000, 32_000, 44_100, 48_000],
)
_RB_FORMATS["mp3"].update(
    {
        "bitrate_bps": [16_000, 320_000],
        "encoding": "MPEG-1/MPEG-2 Audio Layer III; CBR or VBR",
        "bitrate_by_sample_rate": [
            {"sample_rates_hz": [16_000, 22_050, 24_000], "bitrate_bps": [16_000, 160_000]},
            {"sample_rates_hz": [32_000, 44_100, 48_000], "bitrate_bps": [32_000, 320_000]},
        ],
    }
)
_RB_FORMATS["aac"]["bitrate_bps"] = [8_000, 320_000]

_PROFILES = {
    "rekordbox": {
        "name": "rekordbox app import",
        "documentation_version": "7.2.18",
        "audio_formats": _RB_FORMATS,
        "numeric_limits_basis": "manufacturer_manual_pages_260_261",
        "docs": [
            {
                "title": "rekordbox 7.2.18 manual, pages 13-14, 41, 260-261",
                "url": _REKORDBOX_MANUAL,
            },
        ],
        "notes": [
            "App playback formats differ from standalone player formats.",
            "Recheck the installed version and perform a native import/analysis trial.",
            "Only engine-inspectable filename extensions are included in this subset.",
        ],
    },
    "serato": {
        "name": "Serato DJ Pro app import",
        "documentation_version": None,
        "audio_formats": _formats([44_100, 48_000], [44_100, 48_000]),
        "numeric_limits_basis": "conservative_engine_policy_not_vendor_maxima",
        "vendor_numeric_limits_established": False,
        "docs": [
            {"title": "Serato DJ Pro supported file types", "url": _SERATO_FORMATS},
            {
                "title": "Adding files to the Serato DJ Pro library",
                "url": "https://support.serato.com/hc/en-us/articles/223446528-Adding-files-to-the-Serato-DJ-Pro-Library",
            },
            {
                "title": "Serato DJ Pro analysis, including version-specific behavior",
                "url": "https://support.serato.com/hc/en-us/articles/14361068095759-Analyzing-Files",
            },
        ],
        "notes": [
            "Numeric bounds are the engine's conservative common-format subset, not "
            "a statement that Serato rejects all files outside those bounds.",
            "The native app trial remains required for the installed version and OS.",
            "Raw AAC is macOS-only per Serato; this subset admits AAC LC in .m4a only.",
            "OGG and other vendor-supported formats outside engine inspection are not admitted.",
        ],
    },
}


def app_profiles() -> dict:
    """Return independent JSON-compatible profiles without any USB/player promise."""
    profiles = deepcopy(_PROFILES)
    for app, profile in profiles.items():
        profile.update(
            {
                "id": app,
                "researched_at": "2026-10-04",
                "scope": "native_app_input_subset_only",
                "assessed_channels": [1, 2],
                "channel_limits_basis": "conservative_engine_subset",
                "native_automation_available": False,
                "native_trial_required": True,
                "hardware_profile_required": False,
                "standalone_usb_compatibility_assessed": False,
                "musical_analysis_accuracy_assessed": False,
            }
        )
    return profiles


def _positive_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if isinstance(value, str) and value.isascii() and value.isdecimal() and int(value) > 0:
        return int(value)
    return None


def assess_app_track(app: str, properties: dict, file_extension: str) -> list[str]:
    """Reject unknown or out-of-subset properties; never convert or claim app success."""
    if app not in _PROFILES:
        raise AppError("APP_TARGET_UNKNOWN", f"Unknown native app profile: {app}")
    codec = str(properties.get("codec") or "").strip().lower()
    if not codec:
        return ["APP_CODEC_UNKNOWN"]
    pcm_match = re.fullmatch(r"pcm_(?:s)?(\d+)(?:le|be)?", codec)
    family = "pcm" if pcm_match else codec
    spec = _PROFILES[app]["audio_formats"].get(family)
    if spec is None:
        return ["APP_CODEC_OUTSIDE_SUBSET"]
    issues = []
    channels = _positive_int(properties.get("channels"))
    if channels is None:
        issues.append("APP_CHANNELS_UNKNOWN")
    elif channels not in {1, 2}:
        issues.append("APP_CHANNELS_OUTSIDE_SUBSET")
    extension = str(file_extension or "").strip().lower()
    if not extension:
        issues.append("APP_EXTENSION_UNKNOWN")
    elif extension not in spec["extensions"]:
        issues.append("APP_EXTENSION_OUTSIDE_SUBSET")
    rate = _positive_int(properties.get("sample_rate"))
    if rate is None:
        issues.append("APP_SAMPLE_RATE_UNKNOWN")
    elif rate not in spec["sample_rates_hz"]:
        issues.append("APP_SAMPLE_RATE_OUTSIDE_SUBSET")
    if "bit_depths" in spec:
        depth = _positive_int(properties.get("bit_depth"))
        if depth is None:
            issues.append("APP_BIT_DEPTH_UNKNOWN")
        elif depth not in spec["bit_depths"]:
            issues.append("APP_BIT_DEPTH_OUTSIDE_SUBSET")
        if pcm_match:
            encoded_depth = int(pcm_match.group(1))
            if encoded_depth not in spec["bit_depths"]:
                if "APP_BIT_DEPTH_OUTSIDE_SUBSET" not in issues:
                    issues.append("APP_BIT_DEPTH_OUTSIDE_SUBSET")
            elif depth in spec["bit_depths"] and encoded_depth != depth:
                issues.append("APP_BIT_DEPTH_CONFLICT")
    if "bitrate_bps" in spec:
        bitrate = _positive_int(properties.get("bitrate_bps"))
        limits = spec["bitrate_bps"]
        for variant in spec.get("bitrate_by_sample_rate", []):
            if rate in variant["sample_rates_hz"]:
                limits = variant["bitrate_bps"]
                break
        if bitrate is None:
            issues.append("APP_BITRATE_UNKNOWN")
        elif not limits[0] <= bitrate <= limits[1]:
            issues.append("APP_BITRATE_OUTSIDE_SUBSET")
    if family == "aac":
        profile = str(properties.get("codec_profile") or "").strip().upper()
        if not profile:
            issues.append("APP_CODEC_PROFILE_UNKNOWN")
        elif profile != "LC":
            issues.append("APP_CODEC_PROFILE_OUTSIDE_SUBSET")
    return issues
