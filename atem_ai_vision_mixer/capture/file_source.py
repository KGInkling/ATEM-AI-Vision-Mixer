"""Decode synchronized video and audio from a recorded media file."""

from __future__ import annotations

import time
from collections.abc import Iterator
from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any

from atem_ai_vision_mixer.capture.source import CapturedFrame

if TYPE_CHECKING:
    import numpy as np


class FileSource:
    """Read recorded media through the same interface future live capture uses."""

    def __init__(self, path: str | Path, realtime: bool = False) -> None:
        """Remember the file and whether playback should follow its recorded timing."""
        self.path = Path(path)
        self.realtime = realtime

    def __iter__(self) -> Iterator[CapturedFrame]:
        """Yield BGR frames with PCM chunks aligned by presentation timestamp."""
        with _open_container(self.path) as container:
            video_stream, audio_stream = _select_streams(container)
            selected_streams = (video_stream,)
            if audio_stream is not None:
                selected_streams = (video_stream, audio_stream)

            pending_video: tuple[np.ndarray, float] | None = None
            pending_audio: list[tuple[float, np.ndarray]] = []
            first_video_pts = 0.0
            playback_started_at = 0.0

            for packet in container.demux(*selected_streams):
                for frame in packet.decode():
                    frame_pts = _timestamp_seconds(frame)

                    if packet.stream.type == "audio":
                        pending_audio.append((frame_pts, frame.to_ndarray()))
                        continue

                    video = frame.to_ndarray(format="bgr24")
                    if pending_video is not None:
                        audio, pending_audio = _take_audio_before(
                            pending_audio,
                            frame_pts,
                        )
                        previous_video, previous_pts = pending_video
                        self._pace(previous_pts, first_video_pts, playback_started_at)
                        yield CapturedFrame(
                            video=previous_video,
                            audio=audio,
                            pts=previous_pts,
                        )
                    else:
                        first_video_pts = frame_pts
                        playback_started_at = time.monotonic() if self.realtime else 0.0

                    pending_video = (video, frame_pts)

            if pending_video is None:
                return

            video, video_pts = pending_video
            remaining_audio = _combine_audio(
                [chunk for _, chunk in sorted(pending_audio, key=lambda item: item[0])]
            )
            self._pace(video_pts, first_video_pts, playback_started_at)
            yield CapturedFrame(video=video, audio=remaining_audio, pts=video_pts)

    def _pace(
        self,
        frame_pts: float,
        first_frame_pts: float,
        playback_started_at: float,
    ) -> None:
        if not self.realtime:
            return

        recorded_elapsed = max(0.0, frame_pts - first_frame_pts)
        actual_elapsed = time.monotonic() - playback_started_at
        delay = recorded_elapsed - actual_elapsed
        if delay > 0.0:
            time.sleep(delay)


def _open_container(path: Path) -> Any:
    try:
        av = import_module("av")
    except ModuleNotFoundError as error:
        raise ModuleNotFoundError(
            "FileSource requires the perception extra: pip install '.[perception]'"
        ) from error

    return av.open(str(path))


def _select_streams(container: Any) -> tuple[Any, Any | None]:
    if not container.streams.video:
        raise ValueError("FileSource requires a recorded file with a video stream")

    video_stream = container.streams.video[0]
    audio_stream = None
    if container.streams.audio:
        audio_stream = container.streams.audio[0]

    return video_stream, audio_stream


def _timestamp_seconds(frame: Any) -> float:
    if frame.pts is None or frame.time_base is None:
        raise ValueError("Decoded media frame has no presentation timestamp")

    return float(frame.pts * frame.time_base)


def _take_audio_before(
    pending_audio: list[tuple[float, np.ndarray]],
    end_pts: float,
) -> tuple[np.ndarray | None, list[tuple[float, np.ndarray]]]:
    ready: list[tuple[float, np.ndarray]] = []
    future: list[tuple[float, np.ndarray]] = []

    for audio_pts, chunk in pending_audio:
        if audio_pts < end_pts:
            ready.append((audio_pts, chunk))
        else:
            future.append((audio_pts, chunk))

    ready.sort(key=lambda item: item[0])
    audio = _combine_audio([chunk for _, chunk in ready])
    return audio, future


def _combine_audio(chunks: list[np.ndarray]) -> np.ndarray | None:
    if not chunks:
        return None
    if len(chunks) == 1:
        return chunks[0]

    numpy = import_module("numpy")
    return numpy.concatenate(chunks, axis=-1)
