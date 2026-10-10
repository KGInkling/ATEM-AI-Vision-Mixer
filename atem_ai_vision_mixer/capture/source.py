"""Shared capture records and the interface implemented by every source."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from math import isfinite
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    import numpy as np


@dataclass(frozen=True)
class AudioMetadata:
    """Describe native PCM encoding and its exact sample interval without PyAV."""

    sample_rate: int
    sample_format: str
    layout: str
    pts: float
    samples: int

    def __post_init__(self) -> None:
        """Reject metadata that cannot describe a timed PCM chunk."""
        if (
            type(self.sample_rate) is not int
            or self.sample_rate <= 0
            or type(self.samples) is not int
            or self.samples <= 0
            or not isfinite(self.pts)
            or not self.sample_format
            or not self.layout
        ):
            raise ValueError(
                "Audio metadata requires a rate, format, layout, timestamp, and samples"
            )

    @property
    def end_pts(self) -> float:
        """Return the time just after the chunk's last sample on the file timeline."""
        return self.pts + self.samples / self.sample_rate


@dataclass
class CapturedFrame:
    """Keep BGR video and optional PCM audio aligned to one presentation time."""

    video: np.ndarray | Mapping[int, np.ndarray]
    audio: np.ndarray | None
    pts: float
    audio_metadata: AudioMetadata | None = None


class FrameSource(Protocol):
    """Define the seam that recorded files and live capture both implement."""

    def __iter__(self) -> Iterator[CapturedFrame]:
        """Yield synchronized capture records until the source is exhausted."""
        ...
