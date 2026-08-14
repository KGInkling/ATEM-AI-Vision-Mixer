"""Run the offline mixer against a named scripted scenario."""

import argparse
import sys
from collections.abc import Sequence
from typing import TextIO

from atem_ai_vision_mixer.director.rules import RulesDirector
from atem_ai_vision_mixer.execution.controller import Controller, Decision, Mode
from atem_ai_vision_mixer.execution.fake_switcher import FakeSwitcher
from atem_ai_vision_mixer.scenarios import load_scenario, scenario_names
from atem_ai_vision_mixer.world_state import WorldState


def run_scenario(
    scenario_name: str,
    mode: Mode | str = Mode.SHADOW,
    output: TextIO | None = None,
) -> list[Decision]:
    """Replay one scenario and write each observable controller decision as JSONL."""
    scenario_steps = load_scenario(scenario_name)
    initial_state = scenario_steps[0].state
    switcher = FakeSwitcher(
        program=initial_state.program.live_camera,
        preview=_initial_preview_camera(initial_state),
    )
    director = RulesDirector()
    controller = Controller(switcher=switcher, mode=mode)
    output_stream = output if output is not None else sys.stdout
    decisions: list[Decision] = []

    for step in scenario_steps:
        if step.operator_take_camera is not None:
            switcher.simulate_operator_take(step.operator_take_camera)

        shot_intent = director.decide(step.state)
        decision = controller.tick(step.state, shot_intent, now=step.state.timestamp)
        print(decision.model_dump_json(), file=output_stream)
        decisions.append(decision)

    return decisions


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line choices and run the requested offline replay."""
    parser = _argument_parser()
    arguments = parser.parse_args(argv)
    run_scenario(arguments.scenario, arguments.mode)
    return 0


def _argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay the ATEM AI vision mixer without hardware.",
    )
    parser.add_argument(
        "--scenario",
        choices=scenario_names(),
        required=True,
        help="scripted service timeline to replay",
    )
    parser.add_argument(
        "--mode",
        choices=tuple(mode.value for mode in Mode),
        default=Mode.SHADOW.value,
        help="maximum switcher control level (default: shadow)",
    )
    return parser


def _initial_preview_camera(state: WorldState) -> int:
    for camera_id in sorted(state.cameras):
        if camera_id != state.program.live_camera:
            return camera_id

    return state.program.live_camera


if __name__ == "__main__":
    raise SystemExit(main())
