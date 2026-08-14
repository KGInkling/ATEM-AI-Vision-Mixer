"""Tests for the offline world-state scenario fixtures."""

import pytest

from atem_ai_vision_mixer.scenarios import load_scenario, scenario_names


def test_exposes_the_four_required_scenarios_in_stable_order() -> None:
    """Verify the CLI and integration tests see the complete spec-defined catalog."""
    assert scenario_names() == (
        "sermon",
        "camera_fails",
        "operator_takeover",
        "never_pauses",
    )


@pytest.mark.parametrize("scenario_name", scenario_names())
def test_each_scenario_is_nonempty_and_moves_forward_in_time(
    scenario_name: str,
) -> None:
    """Verify every replay is a usable monotonic controller input sequence."""
    steps = load_scenario(scenario_name)
    timestamps = [step.state.timestamp for step in steps]

    assert steps
    assert timestamps == sorted(timestamps)
    assert len(timestamps) == len(set(timestamps))


def test_loading_a_scenario_returns_fresh_world_states() -> None:
    """Verify one test cannot accidentally alter a later replay's fixture data."""
    first = load_scenario("sermon")
    second = load_scenario("sermon")

    assert first == second
    assert first[0].state is not second[0].state


def test_sermon_contains_speech_breaths_and_sentence_boundaries() -> None:
    """Verify the normal service can exercise graded pause decisions."""
    states = [step.state for step in load_scenario("sermon")]

    assert any(state.audio.speaking for state in states)
    assert any(0 < state.audio.pause_ms < 400 for state in states)
    assert any(400 <= state.audio.pause_ms <= 1200 for state in states)


def test_camera_failure_occurs_while_the_failed_camera_is_live() -> None:
    """Verify the failure replay models losing the on-air feed mid-service."""
    states = [step.state for step in load_scenario("camera_fails")]

    assert any(
        not state.cameras[state.program.live_camera].feed_healthy for state in states
    )


def test_operator_takeover_marks_the_human_program_change() -> None:
    """Verify the takeover replay can drive the fake outside controller writes."""
    takeover_steps = [
        step
        for step in load_scenario("operator_takeover")
        if step.operator_take_camera is not None
    ]

    assert len(takeover_steps) == 1
    takeover = takeover_steps[0]
    assert takeover.operator_take_camera == takeover.state.program.live_camera


def test_never_pauses_has_no_eligible_speech_pause() -> None:
    """Verify its staged shot can never pass the controller's pause threshold."""
    states = [step.state for step in load_scenario("never_pauses")]

    assert all(state.audio.speaking or state.audio.pause_ms < 400 for state in states)


def test_unknown_scenario_reports_the_available_choices() -> None:
    """Verify programmatic callers receive a useful error instead of an empty replay."""
    with pytest.raises(ValueError, match="Choose from: sermon, camera_fails"):
        load_scenario("missing")
