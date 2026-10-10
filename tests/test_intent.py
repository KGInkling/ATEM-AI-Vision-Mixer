"""Tests for the validated shot-intent and advisory-policy contracts."""

import json

import pytest
from pydantic import ValidationError

from atem_ai_vision_mixer.director.intent import (
    Reason,
    ShotIntent,
    shot_intent_schema,
)
from atem_ai_vision_mixer.director.policy import (
    DEFAULT_POLICY,
    CutTempo,
    Segment,
    ShotPolicy,
    ShotType,
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


@pytest.fixture
def policy_fields() -> dict:
    """Provide one valid policy so rejection tests change only the disputed field."""
    return {
        "segment": "sermon",
        "preferred_shot": "tight",
        "cut_tempo": "slow",
        "rationale": "Favor tight shots at sentence pauses.",
    }


def test_policy_accepts_json_and_round_trips_as_closed_vocabulary(policy_fields):
    policy = ShotPolicy.model_validate_json(json.dumps(policy_fields))

    assert policy.segment is Segment.SERMON
    assert policy.preferred_shot is ShotType.TIGHT
    assert policy.cut_tempo is CutTempo.SLOW
    assert json.loads(policy.model_dump_json()) == policy_fields
    assert ShotPolicy.model_validate_json(policy.model_dump_json()) == policy


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("segment", "invented_segment"),
        ("preferred_shot", "pan"),
        ("cut_tempo", "rapid"),
    ],
)
def test_policy_rejects_unknown_enum_values(policy_fields, field, value):
    policy_fields[field] = value

    with pytest.raises(ValidationError) as error:
        ShotPolicy.model_validate_json(json.dumps(policy_fields))

    assert error.value.errors()[0]["loc"] == (field,)
    assert error.value.errors()[0]["type"] == "enum"


@pytest.mark.parametrize(
    "field", ["segment", "preferred_shot", "cut_tempo", "rationale"]
)
def test_policy_requires_every_field(policy_fields, field):
    del policy_fields[field]

    with pytest.raises(ValidationError) as error:
        ShotPolicy.model_validate(policy_fields)

    assert error.value.errors()[0]["loc"] == (field,)
    assert error.value.errors()[0]["type"] == "missing"


def test_policy_rejects_extra_fields(policy_fields):
    policy_fields["camera"] = 2

    with pytest.raises(ValidationError) as error:
        ShotPolicy.model_validate(policy_fields)

    assert error.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize("rationale", ["", " \t\n ", "a" * 241, None, 12])
def test_policy_rejects_blank_oversized_or_non_text_rationale(policy_fields, rationale):
    policy_fields["rationale"] = rationale

    with pytest.raises(ValidationError) as error:
        ShotPolicy.model_validate(policy_fields)

    assert error.value.errors()[0]["loc"] == ("rationale",)


@pytest.mark.parametrize("rationale", ["a", "a" * 240])
def test_policy_accepts_rationale_length_boundaries(policy_fields, rationale):
    policy_fields["rationale"] = f"  {rationale}\n"

    assert ShotPolicy.model_validate(policy_fields).rationale == rationale


def test_default_policy_is_conservative_before_any_cloud_response():
    assert DEFAULT_POLICY.segment is Segment.UNKNOWN
    assert DEFAULT_POLICY.preferred_shot is ShotType.WIDE
    assert DEFAULT_POLICY.cut_tempo is CutTempo.SLOW
    assert DEFAULT_POLICY.rationale


@pytest.mark.parametrize("use_default", [True, False])
def test_policy_cannot_be_mutated_while_retained(policy_fields, use_default):
    policy = DEFAULT_POLICY if use_default else ShotPolicy.model_validate(policy_fields)
    previous = policy.model_dump_json()

    with pytest.raises(ValidationError) as error:
        policy.cut_tempo = CutTempo.FAST

    assert error.value.errors()[0]["type"] == "frozen_instance"
    assert policy.model_dump_json() == previous


def test_policy_schema_exposes_required_fields_and_closed_enums():
    schema = ShotPolicy.model_json_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {
        "segment",
        "preferred_shot",
        "cut_tempo",
        "rationale",
    }
    assert set(schema["properties"]) == set(schema["required"])
    assert schema["$defs"]["Segment"]["enum"] == [
        "unknown",
        "sermon",
        "worship",
        "prayer",
        "announcements",
        "transition",
    ]
    assert schema["$defs"]["ShotType"]["enum"] == ["wide", "medium", "tight"]
    assert schema["$defs"]["CutTempo"]["enum"] == ["slow", "normal", "fast"]
    assert schema["properties"]["rationale"]["minLength"] == 1
    assert schema["properties"]["rationale"]["maxLength"] == 240
    json.dumps(schema)
