"""Measure camera motion on small grayscale thumbnails."""

import cv2
import numpy as np

THUMBNAIL_WIDTH = 64
THUMBNAIL_HEIGHT = 36


def motion_score(thumbnail: np.ndarray, prev_thumbnail: np.ndarray | None) -> float:
    """Return normalized mean pixel difference, or zero without an earlier frame."""
    for image in (thumbnail, prev_thumbnail):
        if image is not None and (
            image.dtype != np.uint8
            or image.shape != (THUMBNAIL_HEIGHT, THUMBNAIL_WIDTH)
        ):
            raise ValueError("Motion requires 64 by 36 uint8 grayscale thumbnails")
    if prev_thumbnail is None:
        return 0.0

    difference = cv2.absdiff(thumbnail, prev_thumbnail)
    return float(cv2.mean(difference)[0] / 255.0)
