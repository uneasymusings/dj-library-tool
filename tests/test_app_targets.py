"""App-first assessment does not inherit standalone player limitations."""

import pytest

from djlib.domain.errors import AppError
from djlib.exporting.app_targets import app_profiles, assess_app_track


@pytest.mark.parametrize("app", ["rekordbox", "serato"])
@pytest.mark.parametrize(
    "codec,suffix",
    [("pcm_16", ".wav"), ("pcm_s24be", ".aiff"), ("flac", ".flac"), ("alac", ".m4a")],
)
def test_common_lossless_formats_need_no_hardware(app, codec, suffix):
    track = {
        "codec": codec,
        "sample_rate": 44_100,
        "bit_depth": 24 if "24" in codec else 16,
        "channels": 2,
    }
    assert assess_app_track(app, track, suffix) == []


def test_rekordbox_192k_lossless_is_an_app_format_not_a_player_promise():
    track = {"codec": "flac", "sample_rate": 192_000, "bit_depth": 24, "channels": 2}
    assert assess_app_track("rekordbox", track, ".flac") == []
    assert assess_app_track("serato", track, ".flac") == ["APP_SAMPLE_RATE_OUTSIDE_SUBSET"]
    assert app_profiles()["serato"]["vendor_numeric_limits_established"] is False


@pytest.mark.parametrize("app", ["rekordbox", "serato"])
def test_mp3_filename_must_agree_and_lossy_depth_is_not_required(app):
    track = {"codec": "mp3", "sample_rate": 48_000, "bitrate_bps": 320_000, "channels": 2}
    assert assess_app_track(app, track, ".MP3") == []
    assert assess_app_track(app, track, ".flac") == ["APP_EXTENSION_OUTSIDE_SUBSET"]
    assert assess_app_track(app, track, "") == ["APP_EXTENSION_UNKNOWN"]


def test_rekordbox_mpeg2_bitrate_uses_its_documented_range():
    track = {"codec": "mp3", "sample_rate": 22_050, "bitrate_bps": 160_000, "channels": 1}
    assert assess_app_track("rekordbox", track, ".mp3") == []
    track["bitrate_bps"] = 8_000
    assert assess_app_track("rekordbox", track, ".mp3") == ["APP_BITRATE_OUTSIDE_SUBSET"]
    track["bitrate_bps"] = 192_000
    assert assess_app_track("rekordbox", track, ".mp3") == ["APP_BITRATE_OUTSIDE_SUBSET"]


@pytest.mark.parametrize("app", ["rekordbox", "serato"])
def test_aac_requires_measured_lc_and_cross_platform_m4a_subset(app):
    track = {"codec": "aac", "sample_rate": 44_100, "bitrate_bps": 128_000, "channels": 2}
    assert assess_app_track(app, track, ".m4a") == ["APP_CODEC_PROFILE_UNKNOWN"]
    track["codec_profile"] = "HE-AAC"
    assert assess_app_track(app, track, ".m4a") == ["APP_CODEC_PROFILE_OUTSIDE_SUBSET"]
    track["codec_profile"] = "LC"
    assert assess_app_track(app, track, ".m4a") == []
    assert assess_app_track(app, track, ".aac") == ["APP_EXTENSION_OUTSIDE_SUBSET"]


def test_missing_measurements_never_become_defaults():
    assert assess_app_track("rekordbox", {}, ".wav") == ["APP_CODEC_UNKNOWN"]
    assert assess_app_track("rekordbox", {"codec": "flac"}, ".flac") == [
        "APP_CHANNELS_UNKNOWN",
        "APP_SAMPLE_RATE_UNKNOWN",
        "APP_BIT_DEPTH_UNKNOWN",
    ]
    assert assess_app_track(
        "serato", {"codec": "mp3", "sample_rate": 44_100, "channels": 2}, ".mp3"
    ) == ["APP_BITRATE_UNKNOWN"]


def test_multichannel_float_and_conflicting_pcm_metadata_remain_explicit():
    track = {"codec": "pcm_f32le", "sample_rate": 44_100, "channels": 2, "bit_depth": 32}
    assert assess_app_track("rekordbox", track, ".wav") == ["APP_CODEC_OUTSIDE_SUBSET"]
    track.update(codec="pcm_16", bit_depth=16, channels=6)
    assert assess_app_track("rekordbox", track, ".wav") == ["APP_CHANNELS_OUTSIDE_SUBSET"]
    track.update(codec="pcm_24", channels=2)
    assert assess_app_track("rekordbox", track, ".wav") == ["APP_BIT_DEPTH_CONFLICT"]
    track.update(codec="pcm_32")
    assert assess_app_track("rekordbox", track, ".wav") == ["APP_BIT_DEPTH_OUTSIDE_SUBSET"]


def test_profiles_are_independent_and_do_not_claim_native_success():
    profiles = app_profiles()
    for profile in profiles.values():
        assert profile["docs"]
        assert profile["native_trial_required"]
        assert not profile["native_automation_available"]
        assert not profile["hardware_profile_required"]
        assert not profile["standalone_usb_compatibility_assessed"]
        assert not profile["musical_analysis_accuracy_assessed"]
    profiles["serato"]["audio_formats"]["flac"]["sample_rates_hz"].append(192_000)
    assert 192_000 not in app_profiles()["serato"]["audio_formats"]["flac"]["sample_rates_hz"]


def test_unknown_app_is_not_assumed_to_be_rekordbox():
    with pytest.raises(AppError) as error:
        assess_app_track("unsupported_app", {}, ".wav")
    assert error.value.code == "APP_TARGET_UNKNOWN"
