"""Validated strategic guidance for model directors, separate from camera intent."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

# Keep model explanations short enough to inspect in the decision log.
MAX_RATIONALE_LENGTH = 240


class Segment(StrEnum):
    """Describe the service segment without inventing one before guidance arrives."""

    UNKNOWN = "unknown"
    SERMON = "sermon"
    WORSHIP = "worship"
    PRAYER = "prayer"
    ANNOUNCEMENTS = "announcements"
    TRANSITION = "transition"


class ShotType(StrEnum):
    """Express preferred framing without selecting a camera or controlling it."""

    WIDE = "wide"
    MEDIUM = "medium"
    TIGHT = "tight"


class CutTempo(StrEnum):
    """Describe relative cutting pace while leaving timing to the controller."""

    SLOW = "slow"
    NORMAL = "normal"
    FAST = "fast"


class ShotPolicy(BaseModel):
    """Keep advisory guidance validated and immutable while directors retain it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    segment: Segment
    preferred_shot: ShotType
    cut_tempo: CutTempo
    rationale: Annotated[
        str,
        StringConstraints(
            strip_whitespace=True, min_length=1, max_length=MAX_RATIONALE_LENGTH
        ),
    ]


# An unknown segment avoids assuming what is happening before cloud guidance.
DEFAULT_POLICY = ShotPolicy(
    segment=Segment.UNKNOWN,
    preferred_shot=ShotType.WIDE,
    cut_tempo=CutTempo.SLOW,
    rationale="Use conservative framing and pace until cloud guidance is available.",
)
