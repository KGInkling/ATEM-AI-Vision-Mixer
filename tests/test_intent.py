"""Tests for the validated shot-intent contract."""

import json

import pytest
from pydantic import ValidationError

from atem_ai_vision_mixer.director.intent import (
    Reason,
    ShotIntent,
    shot_intent_schema,
)


def test_shot_intent_accepts_a_known_reason() -> None:
    """Verify a director can create a suggestion from the shared reason codes."""
    intent = ShotIntent(
        camera=2,
        confidence=0.85,
        reason=Reason.BETTER_FRAMING,
    )

    assert intent.camera == 2
    assert intent.confidence == 0.85
    assert intent.reason is Reason.BETTER_FRAMING


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_shot_intent_rejects_confidence_outside_the_normalized_range(
    confidence: float,
) -> None:
    """Verify confidence must stay between zero and one."""
    with pytest.raises(ValidationError):
        ShotIntent(camera=1, confidence=confidence, reason=Reason.HOLD)


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_shot_intent_accepts_both_confidence_boundaries(confidence: float) -> None:
    """Verify zero and one are valid confidence values."""
    intent = ShotIntent(camera=1, confidence=confidence, reason=Reason.HOLD)

    assert intent.confidence == confidence


def test_shot_intent_rejects_an_unknown_reason() -> None:
    """Verify directors cannot invent reason codes outside the shared contract."""
    with pytest.raises(ValidationError):
        ShotIntent(camera=1, confidence=0.5, reason="invented_reason")


def test_shot_intent_rejects_extra_fields() -> None:
    """Verify the public contract contains exactly the three specified fields."""
    with pytest.raises(ValidationError):
        ShotIntent(
            camera=1,
            confidence=0.5,
            reason=Reason.HOLD,
            explanation="This field is not part of the contract.",
        )


def test_reason_enum_contains_the_approved_codes() -> None:
    """Verify every director shares the same small vocabulary of explanations."""
    assert [reason.value for reason in Reason] == [
        "hold",
        "better_framing",
        "subject_present",
        "motion",
        "shot_variety",
    ]


def test_shot_intent_schema_limits_camera_choices() -> None:
    """Verify the LLM schema names exactly the currently connected cameras."""
    schema = shot_intent_schema(camera_ids=[1, 2, 3])

    assert schema["properties"]["camera"]["enum"] == [1, 2, 3]
    assert set(schema["properties"]) == {"camera", "confidence", "reason"}
    json.dumps(schema)


def test_each_schema_call_uses_its_own_camera_list() -> None:
    """Verify one runtime camera list cannot leak into a later schema call."""
    first_schema = shot_intent_schema(camera_ids=[1, 2])
    second_schema = shot_intent_schema(camera_ids=[7])

    assert first_schema["properties"]["camera"]["enum"] == [1, 2]
    assert second_schema["properties"]["camera"]["enum"] == [7]
