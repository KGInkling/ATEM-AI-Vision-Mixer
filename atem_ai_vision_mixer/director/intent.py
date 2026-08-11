"""The validated shot suggestion shared by every director implementation."""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

_Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class Reason(StrEnum):
    """List the allowed explanations for a director's shot suggestion."""

    HOLD = "hold"
    BETTER_FRAMING = "better_framing"
    SUBJECT_PRESENT = "subject_present"
    MOTION = "motion"
    SHOT_VARIETY = "shot_variety"


class ShotIntent(BaseModel):
    """Suggest one camera while leaving the final decision to the controller."""

    model_config = ConfigDict(extra="forbid")

    camera: int
    confidence: _Confidence
    reason: Reason


def shot_intent_schema(camera_ids: list[int]) -> dict:
    """Return an LLM schema limited to the cameras that are actually connected."""
    schema = ShotIntent.model_json_schema()

    # This runtime enum stops a future LLM from naming a camera that is not connected.
    schema["properties"]["camera"]["enum"] = list(camera_ids)

    return schema
