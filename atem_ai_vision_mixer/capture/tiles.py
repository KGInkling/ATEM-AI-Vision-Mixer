"""Turn multiview or discrete camera inputs into one camera-to-frame mapping."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np


@dataclass(frozen=True)
class TileRectangle:
    """Describe one configured multiview crop in pixel coordinates."""

    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        """Reject coordinates that cannot describe a usable image region."""
        if self.x < 0 or self.y < 0:
            raise ValueError("Tile coordinates must be zero or greater")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Tile width and height must be greater than zero")


def extract_tiles(
    frame: np.ndarray | Mapping[int, np.ndarray],
    tile_config: Mapping[int, TileRectangle] | None = None,
    *,
    discrete: bool = False,
) -> dict[int, np.ndarray]:
    """Return camera frames without exposing multiview geometry downstream."""
    if discrete:
        if not isinstance(frame, Mapping):
            raise TypeError("Discrete input must be a camera-to-frame mapping")
        return dict(frame)

    if isinstance(frame, Mapping):
        raise TypeError("Multiview input must be one image, not a camera mapping")
    if tile_config is None:
        raise ValueError("Multiview input requires a tile configuration")

    frame_height, frame_width = _frame_dimensions(frame)
    tiles: dict[int, np.ndarray] = {}

    for camera_id, rectangle in tile_config.items():
        right = rectangle.x + rectangle.width
        bottom = rectangle.y + rectangle.height
        if right > frame_width or bottom > frame_height:
            raise ValueError(f"Tile for camera {camera_id} extends outside the frame")

        tiles[camera_id] = frame[
            rectangle.y : bottom,
            rectangle.x : right,
        ]

    return tiles


def _frame_dimensions(frame: np.ndarray) -> tuple[int, int]:
    if len(frame.shape) < 2:
        raise ValueError("Multiview input must have height and width dimensions")

    return int(frame.shape[0]), int(frame.shape[1])
