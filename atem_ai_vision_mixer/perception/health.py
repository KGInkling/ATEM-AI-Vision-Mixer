"""Detect unusable feeds and quality defects without models or switcher access."""

from dataclasses import dataclass, field
from hashlib import sha256

import cv2
import numpy as np

from atem_ai_vision_mixer.perception.motion import (
    THUMBNAIL_HEIGHT,
    THUMBNAIL_WIDTH,
    motion_score,
)

# Initial image thresholds need tuning against recorded footage from the room.
BLACK_MEAN_MAX = 5.0  # Near zero brightness alone is not enough to reject a feed.
BLACK_STDDEV_MAX = 2.0  # A dark scene with detail must remain usable.
FROZEN_MOTION_MAX = 1.0 / 255.0  # Raw hashes must also agree for a freeze.
FROZEN_FRAME_COUNT = 3  # Count frames, including the first observation.
SOFT_VARIANCE_MIN = 20.0  # Low Laplacian variance suggests missing detail.
CRUSHED_LEVEL_MAX = 16  # Dark tail of the 8-bit grayscale histogram.
BLOWN_LEVEL_MIN = 235  # Bright tail of the 8-bit grayscale histogram.
TAIL_FRACTION_MIN = 0.5  # Isolated highlights/shadows are not quality defects.


@dataclass(frozen=True)
class HealthResult:
    """Keep observations and bounded history; the caller stores one per camera."""

    feed_healthy: bool
    defects: tuple[str, ...]
    thumbnail: np.ndarray = field(repr=False, compare=False)
    frame_hash: bytes = field(repr=False)
    identical_frames: int


def check_health(
    tile: np.ndarray, previous: HealthResult | None = None
) -> HealthResult:
    """Inspect a uint8 BGR tile, retaining only its thumbnail/hash for the next tick.

    Pass this camera's previous result, or None at stream start/reset. Only black
    and frozen feeds are unusable; the other defects describe shot quality.
    """
    if (
        tile.dtype != np.uint8
        or tile.ndim != 3
        or tile.shape[2] != 3
        or tile.shape[0] == 0
        or tile.shape[1] == 0
    ):
        raise ValueError("Health requires a nonempty uint8 BGR tile")

    small = cv2.resize(
        tile, (THUMBNAIL_WIDTH, THUMBNAIL_HEIGHT), interpolation=cv2.INTER_AREA
    )
    thumbnail = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    thumbnail.setflags(write=False)

    # Crop views are strided. Hash logical pixels, with dimensions to reset on resize.
    digest = sha256(str(tile.shape).encode("ascii"))
    digest.update(np.ascontiguousarray(tile))
    frame_hash = digest.digest()
    identical_frames = 1
    if (
        previous is not None
        and frame_hash == previous.frame_hash
        and motion_score(thumbnail, previous.thumbnail) <= FROZEN_MOTION_MAX
    ):
        identical_frames = min(previous.identical_frames + 1, FROZEN_FRAME_COUNT)

    defects: list[str] = []
    mean, stddev = cv2.meanStdDev(thumbnail)
    black = mean[0, 0] <= BLACK_MEAN_MAX and stddev[0, 0] <= BLACK_STDDEV_MAX
    frozen = identical_frames >= FROZEN_FRAME_COUNT
    if black:
        defects.append("black")
    if frozen:
        defects.append("frozen")
    if cv2.Laplacian(thumbnail, cv2.CV_64F).var() < SOFT_VARIANCE_MIN:
        defects.append("soft")
    if (
        np.count_nonzero(thumbnail >= BLOWN_LEVEL_MIN) / thumbnail.size
        >= TAIL_FRACTION_MIN
    ):
        defects.append("blown")
    if (
        np.count_nonzero(thumbnail <= CRUSHED_LEVEL_MAX) / thumbnail.size
        >= TAIL_FRACTION_MIN
    ):
        defects.append("crushed")

    return HealthResult(
        feed_healthy=not (black or frozen),
        defects=tuple(defects),
        thumbnail=thumbnail,
        frame_hash=frame_hash,
        identical_frames=identical_frames,
    )
