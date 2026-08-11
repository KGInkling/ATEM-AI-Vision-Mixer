"""Tests for the validated world-state contract."""

import json

import pytest
from pydantic import ValidationError

from atem_ai_vision_mixer.world_state import (
    AudioState,
    CameraState,
    ProgramState,
    WorldState,
)


def test_world_state_round_trips_through_json() -> None:
    """Verify serialized observations can cross a JSON boundary without changing."""
    state = WorldState(
        timestamp=120.5,
        program=ProgramState(live_camera=1, seconds_live=12.0),
        audio=AudioState(speaking=False, pause_ms=650, level=0.35),
        cameras={
            1: CameraState(
                feed_healthy=True,
                framing_score=0.8,
                subject_present=True,
                motion_score=0.1,
                defects=[],
            ),
            2: CameraState(
                feed_healthy=True,
                framing_score=0.6,
                subject_present=True,
                motion_score=0.4,
                defects=["too_wide"],
            ),
        },
    )

    serialized_state = state.model_dump_json()
    json.loads(serialized_state)
    restored_state = WorldState.model_validate_json(serialized_state)

    assert restored_state == state
    assert set(restored_state.cameras) == {1, 2}


@pytest.mark.parametrize("score", [-0.01, 1.01])
@pytest.mark.parametrize("field_name", ["framing_score", "motion_score"])
def test_camera_scores_reject_values_outside_the_normalized_range(
    field_name: str,
    score: float,
) -> None:
    """Verify invalid camera scores fail where the observation is created."""
    camera_data = {
        "feed_healthy": True,
        "framing_score": 0.5,
        "subject_present": True,
        "motion_score": 0.5,
    }
    camera_data[field_name] = score

    with pytest.raises(ValidationError):
        CameraState(**camera_data)


@pytest.mark.parametrize("score", [0.0, 1.0])
def test_camera_scores_accept_both_range_boundaries(score: float) -> None:
    """Verify zero and one are valid normalized camera scores."""
    camera = CameraState(
        feed_healthy=True,
        framing_score=score,
        subject_present=True,
        motion_score=score,
    )

    assert camera.framing_score == score
    assert camera.motion_score == score


def test_camera_defects_default_to_an_independent_empty_list() -> None:
    """Verify feeds without defects do not share mutable list state."""
    first_camera = CameraState(
        feed_healthy=True,
        framing_score=0.8,
        subject_present=True,
        motion_score=0.1,
    )
    second_camera = CameraState(
        feed_healthy=True,
        framing_score=0.7,
        subject_present=True,
        motion_score=0.2,
    )

    first_camera.defects.append("cropped_head")

    assert first_camera.defects == ["cropped_head"]
    assert second_camera.defects == []
