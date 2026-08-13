"""Tests for deterministic rule-based camera selection."""

import pytest

from atem_ai_vision_mixer.director.intent import Reason
from atem_ai_vision_mixer.director.rules import RulesDirector
from atem_ai_vision_mixer.world_state import (
    AudioState,
    CameraState,
    ProgramState,
    WorldState,
)


def _camera(
    framing_score: float,
    *,
    healthy: bool = True,
    subject_present: bool = True,
) -> CameraState:
    return CameraState(
        feed_healthy=healthy,
        framing_score=framing_score,
        subject_present=subject_present,
        motion_score=0.0,
    )


def _world_state(
    cameras: dict[int, CameraState],
    *,
    live_camera: int = 1,
    seconds_live: float = 0.0,
) -> WorldState:
    return WorldState(
        timestamp=10.0,
        program=ProgramState(
            live_camera=live_camera,
            seconds_live=seconds_live,
        ),
        audio=AudioState(speaking=False, pause_ms=500, level=0.2),
        cameras=cameras,
    )


def test_holds_the_live_camera_when_no_cameras_are_present() -> None:
    """Verify an empty observation never produces a missing intent."""
    state = _world_state(cameras={}, live_camera=7)

    intent = RulesDirector().decide(state)

    assert intent.camera == 7
    assert intent.confidence == 1.0
    assert intent.reason is Reason.HOLD


def test_holds_the_live_camera_when_every_feed_is_unhealthy() -> None:
    """Verify broken feeds cannot become director targets."""
    state = _world_state(
        cameras={
            1: _camera(0.1, healthy=False),
            2: _camera(1.0, healthy=False),
        }
    )

    intent = RulesDirector().decide(state)

    assert intent.camera == 1
    assert intent.reason is Reason.HOLD


def test_ignores_an_unhealthy_camera_with_the_highest_score() -> None:
    """Verify visual quality cannot override feed health."""
    state = _world_state(
        cameras={
            1: _camera(0.4),
            2: _camera(1.0, healthy=False),
            3: _camera(0.8),
        }
    )

    intent = RulesDirector().decide(state)

    assert intent.camera == 3
    assert intent.reason is Reason.BETTER_FRAMING


def test_prefers_a_healthy_camera_with_better_framing() -> None:
    """Verify framing remains the strongest component in shot selection."""
    state = _world_state(
        cameras={
            1: _camera(0.4),
            2: _camera(0.9),
        }
    )

    intent = RulesDirector().decide(state)

    assert intent.camera == 2
    assert intent.confidence == pytest.approx(0.92)
    assert intent.reason is Reason.BETTER_FRAMING


def test_subject_presence_can_overcome_a_small_framing_disadvantage() -> None:
    """Verify a visible person can be more useful than a slightly prettier empty shot."""
    state = _world_state(
        cameras={
            1: _camera(0.7, subject_present=False),
            2: _camera(0.6, subject_present=True),
        }
    )

    intent = RulesDirector().decide(state)

    assert intent.camera == 2
    assert intent.confidence == pytest.approx(0.68)
    assert intent.reason is Reason.SUBJECT_PRESENT


def test_staleness_can_choose_shot_variety_after_a_long_hold() -> None:
    """Verify a long-running live shot eventually yields to a comparable challenger."""
    state = _world_state(
        cameras={
            1: _camera(0.75),
            2: _camera(0.7),
        },
        seconds_live=30.0,
    )

    intent = RulesDirector().decide(state)

    assert intent.camera == 2
    assert intent.reason is Reason.SHOT_VARIETY


def test_staleness_penalty_stops_growing_after_its_configured_maximum() -> None:
    """Verify extremely long shots cannot push confidence outside its valid range."""
    state = _world_state(
        cameras={1: _camera(0.0, subject_present=False)},
        seconds_live=600.0,
    )

    intent = RulesDirector().decide(state)

    assert intent.camera == 1
    assert intent.confidence == 0.0
    assert intent.reason is Reason.HOLD


def test_negative_live_duration_does_not_create_a_staleness_bonus() -> None:
    """Verify malformed negative elapsed time cannot improve the live camera score."""
    state = _world_state(
        cameras={
            1: _camera(0.5),
            2: _camera(0.5),
        },
        seconds_live=-10.0,
    )

    intent = RulesDirector().decide(state)

    assert intent.camera == 1
    assert intent.reason is Reason.HOLD


def test_prefers_the_live_camera_when_scores_are_equal() -> None:
    """Verify exact ties do not cause unnecessary shot changes."""
    state = _world_state(
        cameras={
            2: _camera(0.6),
            1: _camera(0.6),
        }
    )

    intent = RulesDirector().decide(state)

    assert intent.camera == 1
    assert intent.reason is Reason.HOLD


def test_equal_challengers_use_the_lowest_camera_number() -> None:
    """Verify insertion order cannot change the winner when the live feed is unavailable."""
    state = _world_state(
        cameras={
            3: _camera(0.8),
            2: _camera(0.8),
        },
        live_camera=1,
    )

    intent = RulesDirector().decide(state)

    assert intent.camera == 2
    assert intent.reason is Reason.SUBJECT_PRESENT


def test_missing_live_camera_uses_framing_reason_for_an_empty_winner() -> None:
    """Verify a healthy empty feed still receives an accurate fallback explanation."""
    state = _world_state(
        cameras={2: _camera(0.8, subject_present=False)},
        live_camera=1,
    )

    intent = RulesDirector().decide(state)

    assert intent.camera == 2
    assert intent.reason is Reason.BETTER_FRAMING


def test_repeated_decisions_are_equal_and_do_not_modify_world_state() -> None:
    """Verify the director is a pure function of its validated input snapshot."""
    state = _world_state(
        cameras={
            1: _camera(0.5),
            2: _camera(0.8),
        }
    )
    original_state = state.model_copy(deep=True)
    director = RulesDirector()

    first_intent = director.decide(state)
    second_intent = director.decide(state)

    assert first_intent == second_intent
    assert state == original_state
