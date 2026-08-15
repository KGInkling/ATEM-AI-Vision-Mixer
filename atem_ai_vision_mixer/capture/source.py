"""Shared capture records and the interface implemented by every source."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import numpy as np


@dataclass
class CapturedFrame:
    """Keep BGR video and optional PCM audio aligned to one presentation time."""

    video: np.ndarray
    audio: np.ndarray | None
    pts: float


class FrameSource(Protocol):
    """Define the seam that recorded files and live capture both implement."""

    def __iter__(self) -> Iterator[CapturedFrame]:
        """Yield synchronized capture records until the source is exhausted."""
        ...
