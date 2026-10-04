"""Compose local clips into synthetic 1080p footage for perception testing."""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import ExitStack
from dataclasses import asdict
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

from atem_ai_vision_mixer.capture.tiles import TileRectangle

WIDTH, HEIGHT = 1920, 1080


def layout_tiles(layout: str) -> dict[int, TileRectangle]:
    """Return known synthetic crops, not unverified hardware measurements."""
    rectangles = [(0, 0, 960, 540), (960, 0, 960, 540), (0, 540, 960, 540)]
    if layout == "4-up":
        rectangles.append((960, 540, 960, 540))
    elif layout == "7-up":
        rectangles.extend(
            [
                (960, 540, 480, 270),
                (1440, 540, 480, 270),
                (960, 810, 480, 270),
                (1440, 810, 480, 270),
            ]
        )
    else:
        raise ValueError("layout must be 4-up or 7-up")
    return {index + 1: TileRectangle(*rect) for index, rect in enumerate(rectangles)}


def _timestamp(frame: av.VideoFrame | av.AudioFrame) -> Fraction:
    if frame.pts is None or frame.time_base is None:
        raise ValueError("Input media requires presentation timestamps")
    return frame.pts * frame.time_base


def _fit(frame: av.VideoFrame, rectangle: TileRectangle) -> np.ndarray:
    scale = min(rectangle.width / frame.width, rectangle.height / frame.height)
    width = max(1, round(frame.width * scale))
    height = max(1, round(frame.height * scale))
    pixels = frame.reformat(width=width, height=height, format="bgr24").to_ndarray()
    tile = np.zeros((rectangle.height, rectangle.width, 3), dtype=np.uint8)
    x, y = (rectangle.width - width) // 2, (rectangle.height - height) // 2
    tile[y : y + height, x : x + width] = pixels
    return tile


def _video_tiles(
    frames: Iterator[av.VideoFrame],
    first: av.VideoFrame,
    rectangle: TileRectangle,
    fps: int,
    input_rate: Fraction | None,
) -> Iterator[np.ndarray]:
    """Sample by recorded PTS, holding or dropping frames to match output cadence."""
    origin = previous = _timestamp(first)
    current = first
    pixels = _fit(current, rectangle)
    tick = 0
    for following in frames:
        timestamp = _timestamp(following)
        if timestamp <= previous:
            raise ValueError("Video timestamps must increase")
        while Fraction(tick, fps) < timestamp - origin:
            yield pixels
            tick += 1
        current, previous = following, timestamp
        pixels = _fit(current, rectangle)

    if current.duration:
        duration = current.duration * current.time_base
    elif input_rate and input_rate > 0:
        duration = 1 / input_rate
    else:
        raise ValueError("Video needs a final frame duration or a reported frame rate")
    while Fraction(tick, fps) < previous - origin + duration:
        yield pixels
        tick += 1


def _audio_slice(frame: av.AudioFrame, start: int, stop: int) -> av.AudioFrame:
    channels = len(frame.layout.channels)
    samples = frame.to_ndarray()[:, start * channels : stop * channels].copy()
    result = av.AudioFrame.from_ndarray(samples, format="s16", layout=frame.layout.name)
    result.sample_rate = frame.sample_rate
    result.time_base = Fraction(1, frame.sample_rate)
    result.pts = frame.pts + start
    return result


def _audio_frames(container: av.container.InputContainer, origin: Fraction):
    """Preserve the selected track's offset, rate, and channels as packed PCM16."""
    resampler = av.AudioResampler(format="s16")
    for original in container.decode(audio=0):
        _timestamp(original)
        for frame in resampler.resample(original):
            start = round((_timestamp(frame) - origin) * frame.sample_rate)
            frame.time_base = Fraction(1, frame.sample_rate)
            frame.pts = start
            trim = max(0, -start)
            if trim < frame.samples:
                yield _audio_slice(frame, trim, frame.samples)
    # Only sample format changes; the rate is unchanged and no delayed samples remain.


def make_multiview(
    inputs: Sequence[str | Path],
    output: str | Path,
    *,
    layout: str = "4-up",
    audio_input: int = 1,
    fps: int = 30,
) -> dict[int, TileRectangle]:
    """Tile clips on a shared timeline and return crops for the occupied slots.

    Video begins at each clip's first video frame and ends with the shortest clip,
    rounded to the output cadence. Program audio keeps its offset to the selected
    clip's video and is trimmed to that output span. Existing files are never replaced.
    """
    rectangles = layout_tiles(layout)
    if not 1 <= len(inputs) <= len(rectangles):
        raise ValueError(f"Supply between 1 and {len(rectangles)} input clips")
    if fps <= 0:
        raise ValueError("fps must be greater than zero")
    if not 1 <= audio_input <= len(inputs):
        raise ValueError("audio input must name an input clip, starting at 1")
    output = Path(output)
    if output.suffix.lower() != ".mkv":
        raise ValueError("Output must have a .mkv extension (Matroska)")
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Output already exists: {output}")
    paths = [Path(path).resolve() for path in inputs]
    for path in paths:
        if not path.is_file():
            raise FileNotFoundError(f"Missing local input file: {path}")
    config = {camera: rectangles[camera] for camera in range(1, len(paths) + 1)}

    with ExitStack() as stack:
        sources = [stack.enter_context(av.open(str(path))) for path in paths]
        timelines = []
        origins = []
        for source, rectangle in zip(sources, config.values(), strict=True):
            if not source.streams.video:
                raise ValueError("Each input clip must contain a video stream")
            frames = source.decode(video=0)
            first = next(frames, None)
            if first is None:
                raise ValueError("Each input clip must contain decoded video frames")
            origins.append(_timestamp(first))
            timelines.append(
                _video_tiles(
                    frames,
                    first,
                    rectangle,
                    fps,
                    source.streams.video[0].average_rate,
                )
            )

        # A separate reader avoids losing audio while independently sampling video.
        audio_source = stack.enter_context(av.open(str(paths[audio_input - 1])))
        if not audio_source.streams.audio:
            raise ValueError("Selected audio input has no audio stream")
        audio = _audio_frames(audio_source, origins[audio_input - 1])
        pending = next(audio, None)
        if pending is None:
            raise ValueError("Selected audio input has no audio after video begins")
        audio_start = pending.pts

        # Publish a completed file atomically; even a concurrent writer cannot be overwritten.
        with tempfile.NamedTemporaryFile(
            dir=output.parent,
            prefix=f".{output.name}.",
            suffix=".mkv",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            with av.open(str(temporary_path), "w", format="matroska") as container:
                video = container.add_stream("libx264", rate=fps)
                video.width, video.height, video.pix_fmt = WIDTH, HEIGHT, "yuv420p"
                video.options = {
                    "preset": "ultrafast",
                    "crf": "18",
                    "tune": "zerolatency",
                }
                sound = container.add_stream("pcm_s16le", rate=pending.sample_rate)
                sound.layout = pending.layout.name
                rate = pending.sample_rate
                frame_count = 0
                for tiles in zip(*timelines):
                    pixels = np.zeros((HEIGHT, WIDTH, 3), dtype=np.uint8)
                    for tile, rectangle in zip(tiles, config.values(), strict=True):
                        pixels[
                            rectangle.y : rectangle.y + rectangle.height,
                            rectangle.x : rectangle.x + rectangle.width,
                        ] = tile
                    frame = av.VideoFrame.from_ndarray(pixels, format="bgr24")
                    frame.pts, frame.time_base = frame_count, Fraction(1, fps)
                    container.mux(video.encode(frame))
                    frame_count += 1
                    end_sample = frame_count * rate // fps
                    while (
                        pending is not None
                        and pending.pts + pending.samples <= end_sample
                    ):
                        container.mux(sound.encode(pending))
                        pending = next(audio, None)

                end_sample = frame_count * rate // fps
                if audio_start >= end_sample:
                    raise ValueError(
                        "Selected audio does not overlap the composed video"
                    )
                if pending is not None and pending.pts < end_sample:
                    container.mux(
                        sound.encode(_audio_slice(pending, 0, end_sample - pending.pts))
                    )
                container.mux(video.encode(None))
                container.mux(sound.encode(None))
            os.link(temporary_path, output)
        finally:
            temporary_path.unlink(missing_ok=True)
    return config


def main(argv: Sequence[str] | None = None) -> int:
    """Run the local-only converter and print JSON crops for extract_tiles."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs", type=Path, nargs="+", help="Local clips in camera order"
    )
    parser.add_argument("--output", type=Path, required=True, help="New .mkv file")
    parser.add_argument("--layout", choices=("4-up", "7-up"), default="4-up")
    parser.add_argument(
        "--audio-input", type=int, default=1, help="Program audio source (1-based)"
    )
    parser.add_argument(
        "--fps", type=int, default=30, help="Output frame rate (default: 30)"
    )
    args = parser.parse_args(argv)
    try:
        config = make_multiview(
            args.inputs,
            args.output,
            layout=args.layout,
            audio_input=args.audio_input,
            fps=args.fps,
        )
    except (OSError, ValueError, av.FFmpegError) as error:
        parser.error(str(error))
    print(
        json.dumps(
            {camera: asdict(rectangle) for camera, rectangle in config.items()},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
