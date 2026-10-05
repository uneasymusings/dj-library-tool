"""Real audio verifies metadata travels to working copies without touching originals."""

import json
import shutil
import subprocess
from pathlib import Path

import mutagen
import pytest
from mutagen.id3 import ID3
from mutagen.mp4 import MP4

from djlib.application import delivery, organization
from djlib.audio.inspection import checksum, inspect_audio
from djlib.domain.contracts import DeliveryRequest
from djlib.domain.organization_contracts import AnnotationRequest
from djlib.exporting.delivery_media import pcm_hash, prepare_media
from tests.conftest import execute, submit_collection

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="Metadata handoff requires real audio decoders",
)

ANNOTATIONS = {
    "bpm": {"value": 122.5, "source": "operator", "verified": False},
    "key": {"value": "8A", "source": "operator", "verified": True},
    "genres": ["House"],
    "notes": "Warm opening; check the first downbeat.",
    "tags": ["warm", "vocal"],
    "set_role": "opening",
    "energy": 3,
}


@pytest.mark.parametrize(
    ("extension", "codec"),
    [
        ("wav", "pcm_s16le"),
        ("aiff", "pcm_s16be"),
        ("flac", "flac"),
        ("mp3", "libmp3lame"),
        ("m4a", "aac"),
    ],
)
def test_annotations_preserve_audio_and_originals_in_five_formats(
    application, audio_factory, extension, codec
):
    original = audio_factory("base.wav", frames=44100)
    source = original.with_name("tag-source." + extension)
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-n",
            "-i",
            str(original),
            "-c:a",
            codec,
            str(source),
        ],
        capture_output=True,
        timeout=30,
        check=True,
    )
    before = checksum(source)
    properties = inspect_audio(source)
    track = {
        "recording_id": "original-tone",
        "asset_revision_id": "original-bytes",
        "artist": "Original Artist",
        "title": "Tone",
        "version": "Trial",
        "sha256": before,
        "properties": properties.as_dict(),
        "dj_metadata": {"annotations": ANNOTATIONS, "annotation_revision": 1},
    }
    prepared = prepare_media(source, track, application.workspace.exports / extension, "preserve")
    output = Path(prepared["path"])
    assert checksum(source) == before
    assert checksum(output) == prepared["sha256"]
    assert pcm_hash(output) == pcm_hash(source)
    assert prepared["dj_metadata"]["annotations"] == ANNOTATIONS
    assert prepared["metadata_handoff"]["written_fields"] == [
        "artist",
        "title",
        "bpm",
        "key",
        "genres",
        "comments",
    ]
    assert not prepared["metadata_handoff"]["native_analysis_performed"]
    media = mutagen.File(output)
    if isinstance(media.tags, ID3):
        assert str(media.tags["TBPM"]) == "122.5"
        assert str(media.tags["TKEY"]) == "8A"
        assert str(media.tags["TCON"]) == "House"
        comments = str(media.tags.getall("COMM")[0])
    elif isinstance(media, MP4):
        assert "tmpo" not in media.tags  # Never round fractional BPM silently.
        assert bytes(media["----:com.apple.iTunes:BPM"][0]) == b"122.5"
        assert bytes(media["----:com.apple.iTunes:initialkey"][0]) == b"8A"
        assert media["\u00a9gen"] == ["House"]
        assert prepared["metadata_handoff"]["warnings"]
        comments = media["\u00a9cmt"][0]
    else:
        assert media["bpm"] == ["122.5"]
        assert media["initialkey"] == ["8A"]
        assert media["genre"] == ["House"]
        comments = media["comment"][0]
    assert "Warm opening;" in comments
    assert "tags: warm, vocal; role: opening; energy: 3/10" in comments
    inspected_tags = organization._embedded(output)
    assert inspected_tags["bpm"]["known"] is True
    assert inspected_tags["bpm"]["value"] == 122.5
    assert inspected_tags["key"]["value"] == "8A"
    assert inspected_tags["bpm"]["verified"] is False
    # An explicit empty genre override must not silently inherit the source tag.
    before_working = checksum(output)
    cleared = prepare_media(
        output,
        {**track, "sha256": before_working, "dj_metadata": {"annotations": {"genres": []}}},
        application.workspace.exports / (extension + "-empty-genre"),
        "preserve",
    )
    assert organization._embedded(Path(cleared["path"]))["genres"]["values"] == []
    assert "genres" in cleared["metadata_handoff"]["written_fields"]
    assert checksum(output) == before_working
    assert checksum(source) == before


async def test_delivery_freezes_annotation_revision_before_later_edits(application, audio_factory):
    source = audio_factory("annotated.wav", frames=44100)
    original_hash = checksum(source)
    ingested = await execute(application, submit_collection(application, [source])["job_id"])
    collection_id = ingested["result"]["collection_id"]
    ref = application.collection(collection_id)["tracks"][0]
    common = {"recording_id": ref["recording_id"], "asset_revision_id": ref["asset_revision_id"]}
    organization.annotate_recording(
        application,
        AnnotationRequest(
            **common,
            revision=0,
            idempotency_key="first-annotation",
            **ANNOTATIONS,
        ),
    )
    planned = delivery.create_delivery(
        application,
        DeliveryRequest(
            name="Frozen annotations",
            workflow="serato_import",
            app_version="synthetic-test",
            collection_ids=[collection_id],
            pilot_size=1,
        ),
    )
    organization.annotate_recording(
        application,
        AnnotationRequest(
            **common,
            revision=1,
            idempotency_key="later-annotation",
            notes="Later catalog note",
        ),
    )
    job = delivery.prepare_delivery(application, planned["delivery_id"], 1, "frozen-preparation")
    done = await execute(application, job["job_id"])
    assert done["outcome"] == "complete"
    manifest = json.loads(Path(done["result"]["manifest_path"]).read_text())
    track = manifest["tracks"][0]
    assert track["dj_metadata"]["annotation_revision"] == 1
    assert track["dj_metadata"]["annotations"]["notes"] == ANNOTATIONS["notes"]
    assert not track["dj_metadata"]["acoustic_analysis_performed"]
    assert "Later catalog note" not in str(mutagen.File(track["path"]).tags.getall("COMM"))
    assert checksum(source) == original_hash
