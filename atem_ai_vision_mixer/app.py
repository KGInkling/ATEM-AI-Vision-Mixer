"""Run the offline mixer against a named scripted scenario."""

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from typing import TextIO

from atem_ai_vision_mixer.director.layered import LayeredDirector
from atem_ai_vision_mixer.execution.controller import Controller, Decision, Mode
from atem_ai_vision_mixer.execution.fake_switcher import FakeSwitcher
from atem_ai_vision_mixer.scenarios import load_scenario, scenario_names
from atem_ai_vision_mixer.world_state import WorldState


def run_scenario(
    scenario_name: str,
    mode: Mode | str = Mode.SHADOW,
    output: TextIO | None = None,
    *,
    local_enabled: bool = False,
    cloud_enabled: bool = False,
    aws_region: str | None = None,
    inference_profile: str | None = None,
    aws_profile: str | None = None,
) -> list[Decision]:
    """Replay one scenario and write each observable controller decision as JSONL."""
    scenario_steps = load_scenario(scenario_name)
    initial_state = scenario_steps[0].state
    switcher = FakeSwitcher(
        program=initial_state.program.live_camera,
        preview=_initial_preview_camera(initial_state),
    )
    director = LayeredDirector(
        local_enabled=local_enabled,
        cloud_enabled=cloud_enabled,
        aws_region=aws_region,
        inference_profile=inference_profile,
        aws_profile=aws_profile,
    )
    try:
        controller = Controller(switcher=switcher, mode=mode)
        output_stream = output if output is not None else sys.stdout
        decisions: list[Decision] = []

        for step in scenario_steps:
            if step.operator_take_camera is not None:
                switcher.simulate_operator_take(step.operator_take_camera)

            shot_intent = director.decide(step.state)
            decision = controller.tick(
                step.state, shot_intent, now=step.state.timestamp
            )
            record = {
                **decision.model_dump(mode="json"),
                "director_tier": director.tier,
            }
            print(json.dumps(record, allow_nan=False), file=output_stream)
            decisions.append(decision)

        return decisions
    finally:
        director.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Parse command-line choices and run the requested offline replay."""
    parser = _argument_parser()
    arguments = parser.parse_args(argv)
    if arguments.enable_cloud:
        logging.basicConfig()
        logging.getLogger("atem_ai_vision_mixer.director.cloud_llm").setLevel(
            logging.INFO
        )
    try:
        run_scenario(
            arguments.scenario,
            arguments.mode,
            local_enabled=arguments.enable_local,
            cloud_enabled=arguments.enable_cloud,
            aws_region=arguments.aws_region,
            inference_profile=arguments.inference_profile,
            aws_profile=arguments.aws_profile,
        )
    except ValueError as error:
        parser.error(str(error))
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
    parser.add_argument(
        "--enable-local", action="store_true", help="opt into local Ollama shot advice"
    )
    parser.add_argument(
        "--enable-cloud",
        action="store_true",
        help="opt into Bedrock policy calls and cost",
    )
    parser.add_argument("--aws-region", help="explicit Bedrock source region")
    parser.add_argument(
        "--inference-profile", help="explicit Sonnet 4.6 routing profile"
    )
    parser.add_argument("--aws-profile", help="existing AWS credential profile")
    return parser


def _initial_preview_camera(state: WorldState) -> int:
    for camera_id in sorted(state.cameras):
        if camera_id != state.program.live_camera:
            return camera_id

    return state.program.live_camera


if __name__ == "__main__":
    raise SystemExit(main())
