"""Model distinctions and incomplete metadata must survive delivery planning."""

import pytest

from djlib.domain.errors import AppError
from djlib.exporting.targets import assess_track, target_profiles


def test_legacy_player_rejects_flac_while_newer_player_accepts_96k():
    track = {"channels": 2, "codec": "flac", "sample_rate": 96_000, "bit_depth": 24}
    assert assess_track("cdj-2000nxs", track) == ["TARGET_CODEC_UNSUPPORTED"]
    assert assess_track("cdj-3000", track) == []
    assert assess_track("opus-quad", track) == []
    assert assess_track("xdj-rx3", track) == ["TARGET_SAMPLE_RATE_UNSUPPORTED"]


def test_rx3_does_not_inherit_alac_from_other_current_players():
    track = {"channels": 2, "codec": "alac", "sample_rate": 44_100, "bit_depth": 16}
    assert assess_track("xdj-rx3", track) == ["TARGET_CODEC_UNSUPPORTED"]
    assert assess_track("cdj-3000", track) == []


@pytest.mark.parametrize("codec", ["pcm_16", "pcm_s16le", "pcm_s16be"])
def test_wav_and_ffprobe_integer_pcm_names(codec):
    assert (
        assess_track(
            "cdj-2000nxs",
            {
                "channels": 2,
                "codec": codec,
                "sample_rate": 44_100,
                "bit_depth": 16,
            },
        )
        == []
    )


def test_float_pcm_and_32_bit_pcm_are_not_supported():
    assert assess_track(
        "opus-quad",
        {
            "channels": 2,
            "codec": "pcm_f32le",
            "sample_rate": 44_100,
            "bit_depth": 32,
        },
    ) == ["TARGET_CODEC_UNSUPPORTED"]
    assert assess_track(
        "opus-quad",
        {
            "channels": 2,
            "codec": "pcm_32",
            "sample_rate": 44_100,
            "bit_depth": 32,
        },
    ) == ["TARGET_BIT_DEPTH_UNSUPPORTED"]


def test_legacy_mpeg2_bitrate_constraint_depends_on_sample_rate():
    track = {"channels": 2, "codec": "mp3", "sample_rate": 22_050, "bitrate_bps": 192_000}
    assert assess_track("cdj-2000nxs", track) == ["TARGET_BITRATE_UNSUPPORTED"]
    track["bitrate_bps"] = 128_000
    assert assess_track("cdj-2000nxs", track) == []
    assert assess_track("cdj-3000", track) == ["TARGET_SAMPLE_RATE_UNSUPPORTED"]


def test_contradictory_pcm_metadata_does_not_hide_unsupported_encoding():
    assert assess_track(
        "cdj-3000", {"channels": 2, "codec": "pcm_s32le", "sample_rate": 44_100, "bit_depth": 16}
    ) == ["TARGET_BIT_DEPTH_UNSUPPORTED"]
    assert assess_track(
        "cdj-3000", {"channels": 2, "codec": "pcm_24", "sample_rate": 44_100, "bit_depth": 16}
    ) == ["TARGET_BIT_DEPTH_CONFLICT"]


def test_rx3_accepts_32k_lossy_while_cdj3000_requires_44_or_48k():
    track = {"channels": 2, "codec": "mp3", "sample_rate": 32_000, "bitrate_bps": 320_000}
    assert assess_track("xdj-rx3", track) == []
    assert assess_track("cdj-3000", track) == ["TARGET_SAMPLE_RATE_UNSUPPORTED"]


def test_lossy_decode_depth_is_not_an_encoded_bit_depth_requirement():
    assert (
        assess_track(
            "xdj-rx3",
            {
                "channels": 2,
                "codec": "mp3",
                "sample_rate": 44_100,
                "bitrate_bps": 320_000,
                "bit_depth": None,
            },
        )
        == []
    )


def test_aac_must_have_confirmed_lc_profile():
    track = {"channels": 2, "codec": "aac", "sample_rate": 44_100, "bitrate_bps": 256_000}
    assert assess_track("opus-quad", track) == ["TARGET_CODEC_PROFILE_UNKNOWN"]
    track["codec_profile"] = "HE-AAC"
    assert assess_track("opus-quad", track) == ["TARGET_CODEC_PROFILE_UNSUPPORTED"]
    track["codec_profile"] = "LC"
    assert assess_track("opus-quad", track) == []


def test_missing_or_invalid_metadata_does_not_silently_pass():
    assert assess_track("cdj-3000", {}) == ["TARGET_CODEC_UNKNOWN"]
    assert assess_track("cdj-3000", {"channels": 2, "codec": "flac", "sample_rate": 44_100}) == [
        "TARGET_BIT_DEPTH_UNKNOWN",
    ]
    assert assess_track(
        "cdj-3000", {"channels": 2, "codec": "flac", "sample_rate": True, "bit_depth": 16.9}
    ) == [
        "TARGET_SAMPLE_RATE_UNKNOWN",
        "TARGET_BIT_DEPTH_UNKNOWN",
    ]
    assert assess_track("cdj-3000", {"channels": 2, "codec": "mp3", "sample_rate": "44100"}) == [
        "TARGET_BITRATE_UNKNOWN",
    ]


def test_unknown_target_is_an_actionable_error():
    with pytest.raises(AppError) as error:
        assess_track("unknown-player", {})
    assert error.value.code == "TARGET_UNKNOWN"


@pytest.mark.parametrize("channels", [3, 6, 8])
def test_multichannel_is_outside_the_engine_assessed_subset(channels):
    track = {"codec": "flac", "sample_rate": 44_100, "bit_depth": 16, "channels": channels}
    assert assess_track("cdj-3000", track) == ["TARGET_CHANNELS_UNSUPPORTED"]


@pytest.mark.parametrize("channels", [None, 0, True, "unknown", 2.5])
def test_unknown_or_invalid_channels_cannot_pass(channels):
    track = {"codec": "flac", "sample_rate": 44_100, "bit_depth": 16, "channels": channels}
    assert assess_track("cdj-3000", track) == ["TARGET_CHANNELS_UNKNOWN"]
    del track["channels"]
    assert assess_track("cdj-3000", track) == ["TARGET_CHANNELS_UNKNOWN"]


def test_mono_and_stereo_are_the_explicit_assessed_subset():
    for channels in [1, 2]:
        assert (
            assess_track(
                "cdj-3000",
                {
                    "codec": "flac",
                    "sample_rate": 44_100,
                    "bit_depth": 16,
                    "channels": channels,
                },
            )
            == []
        )
    for profile in target_profiles().values():
        assert profile["assessed_channels"] == [1, 2]
        assert "conservative subset" in profile["channel_assessment_note"]


def test_mp3_bytes_cannot_pass_as_a_flac_filename_when_extension_supplied():
    track = {"codec": "mp3", "sample_rate": 44_100, "bitrate_bps": 320_000, "channels": 2}
    assert assess_track("cdj-2000nxs", track, file_extension=".flac") == [
        "TARGET_EXTENSION_UNSUPPORTED",
    ]
    assert assess_track("cdj-2000nxs", track, file_extension=".MP3") == []
    assert assess_track("cdj-2000nxs", track, file_extension="") == ["TARGET_EXTENSION_UNKNOWN"]
    assert assess_track("cdj-2000nxs", track) == []


def test_lossless_codec_checks_its_own_extension_not_another_supported_codec():
    track = {"codec": "flac", "sample_rate": 44_100, "bit_depth": 16, "channels": 2}
    assert assess_track("cdj-3000", track, file_extension=".m4a") == [
        "TARGET_EXTENSION_UNSUPPORTED",
    ]
    assert assess_track("cdj-3000", track, file_extension=".flac") == []


def test_profiles_preserve_evidence_boundaries_and_cannot_mutate_global_checks():
    profiles = target_profiles()
    assert profiles["cdj-3000"]["library_format"] == "device_library"
    assert profiles["opus-quad"]["library_format"] == "onelibrary"
    assert profiles["xdj-rx3"]["unsupported_partition_schemes"] == ["GPT"]
    for profile in profiles.values():
        assert profile["docs"]
        assert profile["partition_schemes"] is None
        assert profile["partition_scheme_verification_required"]
        assert profile["firmware_verified"] is False
        assert profile["hardware_verified"] is False
    profiles["cdj-3000"]["audio_formats"]["flac"]["bit_depths"].append(32)
    profiles["cdj-3000"]["filesystems"].append("APFS")
    assert "APFS" not in target_profiles()["cdj-3000"]["filesystems"]
    assert assess_track(
        "cdj-3000",
        {
            "channels": 2,
            "codec": "flac",
            "sample_rate": 44_100,
            "bit_depth": 32,
        },
    ) == ["TARGET_BIT_DEPTH_UNSUPPORTED"]
