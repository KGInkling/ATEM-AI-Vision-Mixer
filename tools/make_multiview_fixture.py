"""Create seven short artificial clips without using recordings of real people."""

from __future__ import annotations

import argparse
from fractions import Fraction
from pathlib import Path

import av
import numpy as np

COLORS = (
    (30, 30, 200),
    (30, 200, 30),
    (200, 30, 30),
    (30, 200, 200),
    (200, 30, 200),
    (200, 200, 30),
    (120, 120, 120),
)
FPS, SAMPLE_RATE = 10, 8_000


def make_fixture(directory: Path) -> list[Path]:
    """Write one second of moving video and a distinct tone per camera to a new folder."""
    directory.mkdir(parents=True, exist_ok=False)
    paths = []
    for camera, color in enumerate(COLORS, start=1):
        path = directory / f"camera-{camera}.nut"
        with av.open(str(path), "w", format="nut") as container:
            video = container.add_stream("rawvideo", rate=FPS)
            video.width, video.height, video.pix_fmt = 160, 90, "bgr24"
            audio = container.add_stream("pcm_s16le", rate=SAMPLE_RATE)
            audio.layout = "mono"
            for index in range(FPS):
                pixels = np.empty((90, 160, 3), dtype=np.uint8)
                pixels[:] = color
                left = index * 12
                pixels[30:50, left : left + 20] = 240
                frame = av.VideoFrame.from_ndarray(pixels, format="bgr24")
                frame.pts, frame.time_base = index, Fraction(1, FPS)
                container.mux(video.encode(frame))
                start, end = (
                    index * SAMPLE_RATE // FPS,
                    (index + 1) * SAMPLE_RATE // FPS,
                )
                tone = 4_000 * np.sin(
                    2 * np.pi * (camera * 110) * np.arange(start, end) / SAMPLE_RATE
                )
                chunk = av.AudioFrame.from_ndarray(
                    tone.astype(np.int16)[None, :], format="s16", layout="mono"
                )
                chunk.sample_rate = SAMPLE_RATE
                chunk.pts, chunk.time_base = start, Fraction(1, SAMPLE_RATE)
                container.mux(audio.encode(chunk))
            container.mux(video.encode(None))
            container.mux(audio.encode(None))
        paths.append(path)
    return paths


def main(argv: list[str] | None = None) -> int:
    """Create the reproducible source clips and print their paths for local use."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "directory", type=Path, help="New directory outside the checkout"
    )
    args = parser.parse_args(argv)
    try:
        paths = make_fixture(args.directory)
    except (OSError, ValueError, av.FFmpegError) as error:
        parser.error(str(error))
    print("\n".join(str(path) for path in paths))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
