"""End-to-end safety checks across scenarios, director, controller, and switcher."""

import pytest

from atem_ai_vision_mixer.director.rules import RulesDirector
from atem_ai_vision_mixer.execution.controller import (
    Action,
    Controller,
    ControllerConfig,
    Decision,
    Mode,
)
from atem_ai_vision_mixer.execution.fake_switcher import FakeSwitcher
from atem_ai_vision_mixer.scenarios import (
    ScenarioStep,
    load_scenario,
    scenario_names,
)
from atem_ai_vision_mixer.world_state import WorldState

# Rate limits are defined over an inclusive trailing minute.
TRAILING_MINUTE_SECONDS = 60.0


def _switcher_for(state: WorldState) -> FakeSwitcher:
    preview_camera = state.program.live_camera
    for camera_id in sorted(state.cameras):
        if camera_id != state.program.live_camera:
            preview_camera = camera_id
            break

    return FakeSwitcher(
        program=state.program.live_camera,
        preview=preview_camera,
    )


def _apply_operator_take(
    step: ScenarioStep,
    switchers: tuple[FakeSwitcher, ...],
) -> None:
    if step.operator_take_camera is None:
        return

    for switcher in switchers:
        switcher.simulate_operator_take(step.operator_take_camera)


def _assert_auto_safety_properties(
    decision: Decision,
    state: WorldState,
    config: ControllerConfig,
    commanded_cut_times: list[float],
    switcher: FakeSwitcher,
) -> None:
    if decision.action is Action.CUT:
        assert decision.camera is not None
        assert state.program.seconds_live >= config.min_dwell_seconds
        assert state.cameras[decision.camera].feed_healthy
        assert state.audio.speaking is False

        if commanded_cut_times:
            seconds_since_previous_cut = decision.timestamp - commanded_cut_times[-1]
            assert seconds_since_previous_cut >= config.cut_cooldown_seconds

        commanded_cut_times.append(decision.timestamp)

    cuts_in_trailing_minute = [
        cut_time
        for cut_time in commanded_cut_times
        if decision.timestamp - cut_time <= TRAILING_MINUTE_SECONDS
    ]
    assert len(cuts_in_trailing_minute) <= config.max_cuts_per_minute
    assert switcher.writes.count("cut") == len(commanded_cut_times)


@pytest.mark.parametrize("scenario_name", scenario_names())
def test_each_scenario_preserves_all_safety_properties_after_every_tick(
    scenario_name: str,
) -> None:
    """Verify every complete replay stays safe and deterministic after each step."""
    steps = load_scenario(scenario_name)
    initial_state = steps[0].state

    first_switcher = _switcher_for(initial_state)
    second_switcher = _switcher_for(initial_state)
    shadow_switcher = _switcher_for(initial_state)

    first_config = ControllerConfig()
    second_config = ControllerConfig()
    shadow_config = ControllerConfig()

    first_controller = Controller(first_switcher, config=first_config, mode=Mode.AUTO)
    second_controller = Controller(
        second_switcher, config=second_config, mode=Mode.AUTO
    )
    shadow_controller = Controller(
        shadow_switcher,
        config=shadow_config,
        mode=Mode.SHADOW,
    )

    first_director = RulesDirector()
    second_director = RulesDirector()
    shadow_director = RulesDirector()

    first_decisions: list[Decision] = []
    second_decisions: list[Decision] = []
    commanded_cut_times: list[float] = []

    for step in steps:
        _apply_operator_take(
            step,
            (first_switcher, second_switcher, shadow_switcher),
        )

        first_intent = first_director.decide(step.state)
        second_intent = second_director.decide(step.state)
        shadow_intent = shadow_director.decide(step.state)

        assert first_intent == second_intent == shadow_intent

        first_decision = first_controller.tick(
            step.state,
            first_intent,
            now=step.state.timestamp,
        )
        second_decision = second_controller.tick(
            step.state,
            second_intent,
            now=step.state.timestamp,
        )
        shadow_controller.tick(
            step.state,
            shadow_intent,
            now=step.state.timestamp,
        )

        first_decisions.append(first_decision)
        second_decisions.append(second_decision)

        assert first_decisions == second_decisions
        assert first_switcher.writes == second_switcher.writes
        assert shadow_switcher.writes == []

        _assert_auto_safety_properties(
            first_decision,
            step.state,
            first_config,
            commanded_cut_times,
            first_switcher,
        )
