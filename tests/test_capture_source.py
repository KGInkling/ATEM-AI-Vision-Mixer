"""Tests for the hardware-neutral capture interface."""

from collections.abc import Iterator

import pytest

from atem_ai_vision_mixer.capture.source import (
    AudioMetadata,
    CapturedFrame,
    FrameSource,
)


class ExampleSource:
    """Provide a small iterable used to verify structural protocol compatibility."""

    def __init__(self, frames: list[CapturedFrame]) -> None:
        self.frames = frames

    def __iter__(self) -> Iterator[CapturedFrame]:
        """Yield the configured frames in presentation order."""
        return iter(self.frames)


def _read_all(source: FrameSource) -> list[CapturedFrame]:
    return list(source)


def test_captured_frame_keeps_video_audio_and_timestamp_together() -> None:
    """Verify a capture record preserves its synchronized payloads unchanged."""
    video = object()
    audio = object()

    frame = CapturedFrame(video=video, audio=audio, pts=12.5)

    assert frame.video is video
    assert frame.audio is audio
    assert frame.pts == 12.5


def test_captured_frame_accepts_video_without_an_audio_chunk() -> None:
    """Verify sources can represent a video timestamp with no matching audio chunk."""
    frame = CapturedFrame(video=object(), audio=None, pts=3.25)

    assert frame.audio is None
    assert frame.audio_metadata is None


def test_frame_source_accepts_any_matching_iterable() -> None:
    """Verify consumers can use a source without knowing whether it is file or live."""
    frames = [
        CapturedFrame(video=object(), audio=None, pts=0.0),
        CapturedFrame(video=object(), audio=object(), pts=0.04),
    ]

    assert _read_all(ExampleSource(frames)) == frames


def test_audio_metadata_preserves_native_payload_and_describes_its_interval():
    audio = object()
    metadata = AudioMetadata(44100, "s16", "stereo", 12.5, 4410)
    frame = CapturedFrame(object(), audio, 12.5, metadata)

    assert frame.audio is audio
    assert frame.audio_metadata is metadata
    assert metadata.end_pts == pytest.approx(12.6)


@pytest.mark.parametrize(
    "changes",
    [
        {"sample_rate": 0},
        {"samples": 0},
        {"pts": float("nan")},
        {"sample_format": ""},
        {"layout": ""},
    ],
)
def test_audio_metadata_rejects_unusable_sample_intervals(changes):
    fields = {
        "sample_rate": 16000,
        "sample_format": "fltp",
        "layout": "mono",
        "pts": 0.0,
        "samples": 512,
    }
    fields.update(changes)
    with pytest.raises(ValueError, match="Audio metadata"):
        AudioMetadata(**fields)
