"""Tests for the hardware-neutral capture interface."""

from collections.abc import Iterator

from atem_ai_vision_mixer.capture.source import CapturedFrame, FrameSource


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


def test_frame_source_accepts_any_matching_iterable() -> None:
    """Verify consumers can use a source without knowing whether it is file or live."""
    frames = [
        CapturedFrame(video=object(), audio=None, pts=0.0),
        CapturedFrame(video=object(), audio=object(), pts=0.04),
    ]

    assert _read_all(ExampleSource(frames)) == frames
