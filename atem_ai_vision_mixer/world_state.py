"""Validated snapshot of everything a director can observe."""

from typing import Annotated

from pydantic import BaseModel, Field

_NormalizedScore = Annotated[float, Field(ge=0.0, le=1.0)]


class ProgramState(BaseModel):
    """Describe the camera currently on air and how long it has been live."""

    live_camera: int
    seconds_live: float


class AudioState(BaseModel):
    """Describe whether the speaker is talking and the current audio level."""

    speaking: bool
    pause_ms: int
    level: float


class CameraState(BaseModel):
    """Describe the health and visual quality of one camera feed."""

    feed_healthy: bool
    framing_score: _NormalizedScore
    subject_present: bool
    motion_score: _NormalizedScore
    defects: list[str] = Field(default_factory=list)


class WorldState(BaseModel):
    """Combine program, audio, and camera observations for shot selection."""

    timestamp: float
    program: ProgramState
    audio: AudioState
    cameras: dict[int, CameraState]
