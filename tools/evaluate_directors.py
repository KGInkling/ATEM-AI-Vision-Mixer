"""Compare advisory directors over anonymous recorded observations, on a fake bus."""

import argparse
import hashlib
import json
import logging
from collections import Counter
from collections.abc import Sequence
from contextlib import ExitStack
from math import isfinite
from pathlib import Path
from time import monotonic, sleep

from atem_ai_vision_mixer.director.layered import LayeredDirector
from atem_ai_vision_mixer.director.prompt_context import world_state_summary
from atem_ai_vision_mixer.execution.controller import Action, Controller, Mode
from atem_ai_vision_mixer.execution.fake_switcher import FakeSwitcher
from atem_ai_vision_mixer.world_state import ProgramState, WorldState


def load_recording(path: Path) -> tuple[tuple[WorldState, ...], str]:
    """Validate the entire input before calls, discarding private extras and text."""
    payload = path.read_bytes()
    states = []
    for line_number, line in enumerate(payload.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            state = WorldState.model_validate_json(line)
            summary = world_state_summary(state)
            json.dumps(summary, allow_nan=False)
            state = WorldState.model_validate(summary)
        except ValueError:
            raise ValueError(f"Invalid WorldState at row {line_number}") from None
        if states and state.timestamp <= states[-1].timestamp:
            raise ValueError(f"Timestamps must increase at row {line_number}")
        states.append(state)
    if not states:
        raise ValueError("Recording must contain WorldState snapshots")
    if states[0].program.seconds_live < 0:
        raise ValueError("Initial live duration must be nonnegative")
    if not isfinite(states[-1].timestamp - states[0].timestamp) or not isfinite(
        states[0].timestamp - states[0].program.seconds_live
    ):
        raise ValueError("Recording time range exceeds finite simulation limits")
    return tuple(states), hashlib.sha256(payload).hexdigest()


def replay(
    states: tuple[WorldState, ...], director: LayeredDirector, *, paced: bool
) -> dict:
    """Simulate program feedback; model calls retain their real asynchronous cadence."""
    first = states[0]
    switcher = FakeSwitcher(
        program=first.program.live_camera, preview=first.program.live_camera
    )
    controller = Controller(switcher, mode=Mode.AUTO)
    last_program_change = first.timestamp - first.program.seconds_live
    started = monotonic()
    cuts = []
    tiers: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    try:
        for index, observed in enumerate(states, start=1):
            if paced:
                remaining = (
                    observed.timestamp - first.timestamp - (monotonic() - started)
                )
                if remaining > 0:
                    sleep(remaining)
                now = first.timestamp + (monotonic() - started)
            else:
                now = observed.timestamp
            state = observed.model_copy(
                update={
                    "program": ProgramState(
                        live_camera=switcher.program_input(),
                        seconds_live=max(0.0, now - last_program_change),
                    )
                }
            )
            intent = director.decide(state)
            if paced:
                now = first.timestamp + (monotonic() - started)
            decision = controller.tick(state, intent, now=now)
            tiers[director.tier] += 1
            reasons[decision.reason] += 1
            if decision.action is Action.CUT:
                last_program_change = now
                cuts.append(
                    {
                        "snapshot": index,
                        "recorded_timestamp": observed.timestamp,
                        "processed_timestamp": decision.timestamp,
                        "camera": decision.camera,
                        "director_tier": director.tier,
                    }
                )
        return {
            "cuts": cuts,
            "tier_counts": dict(tiers),
            "reason_counts": dict(reasons),
            "stale_snapshots": reasons["stale_world_state"],
        }
    finally:
        director.close()


def compare_recording(
    path: Path,
    *,
    local_enabled: bool = False,
    cloud_enabled: bool = False,
    aws_region: str | None = None,
    inference_profile: str | None = None,
    aws_profile: str | None = None,
) -> dict:
    """Run rules, then explicitly selected model variants; differences are not quality."""
    if cloud_enabled and not local_enabled:
        raise ValueError("Cloud comparison requires local advice to be enabled")
    states, digest = load_recording(path)
    configurations = [("rules", False, False)]
    if local_enabled:
        configurations.append(("local", True, False))
    if cloud_enabled:
        configurations.append(("cloud_informed", True, True))
    runs = {}
    with ExitStack() as cleanup:
        # Construct all variants first: invalid routing must precede any model call.
        directors = []
        for name, use_local, use_cloud in configurations:
            director = LayeredDirector(
                local_enabled=use_local,
                cloud_enabled=use_cloud,
                aws_region=aws_region,
                inference_profile=inference_profile,
                aws_profile=aws_profile,
            )
            cleanup.callback(director.close)
            directors.append((name, use_local, director))
        for name, paced, director in directors:
            result = replay(states, director, paced=paced)
            result["model_evidence"] = (
                "not_requested"
                if name == "rules"
                else "valid_advice"
                if result["tier_counts"].get(name, 0)
                else "no_valid_advice"
            )
            runs[name] = result
    rules = runs["rules"]["cuts"]
    rules_keys = {(cut["snapshot"], cut["camera"]) for cut in rules}
    differences = {}
    for name, result in runs.items():
        if name == "rules":
            continue
        cuts = result["cuts"]
        keys = {(cut["snapshot"], cut["camera"]) for cut in cuts}
        differences[name] = {
            "matching_cuts": len(keys & rules_keys),
            "only_rules": [
                cut for cut in rules if (cut["snapshot"], cut["camera"]) not in keys
            ],
            "only_variant": [
                cut
                for cut in cuts
                if (cut["snapshot"], cut["camera"]) not in rules_keys
            ],
        }
    return {
        "input_sha256": digest,
        "snapshots": len(states),
        "span_seconds": states[-1].timestamp - states[0].timestamp,
        "switcher": "fake_auto_simulation",
        "program_history": "simulated; later recorded bus changes are not operator actions",
        "runs": runs,
        "differences": differences,
        "quality": "unassessed",
        "quality_note": "Cut differences alone establish no improvement over rules.",
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Default to a rules-only JSON report; opt-in model variants use wall time."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("recording", type=Path, help="recorded WorldState JSONL")
    parser.add_argument(
        "--enable-local", action="store_true", help="include real local model calls"
    )
    parser.add_argument(
        "--enable-cloud", action="store_true", help="include Bedrock calls and cost"
    )
    parser.add_argument("--aws-region")
    parser.add_argument("--inference-profile")
    parser.add_argument("--aws-profile")
    arguments = parser.parse_args(argv)
    if arguments.enable_cloud:
        logging.basicConfig()
        logging.getLogger("atem_ai_vision_mixer.director.cloud_llm").setLevel(
            logging.INFO
        )
    try:
        report = compare_recording(
            arguments.recording,
            local_enabled=arguments.enable_local,
            cloud_enabled=arguments.enable_cloud,
            aws_region=arguments.aws_region,
            inference_profile=arguments.inference_profile,
            aws_profile=arguments.aws_profile,
        )
    except OSError:
        parser.error("Cannot read the WorldState recording")
    except ValueError as error:
        parser.error(str(error))
    print(json.dumps(report, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
