"""Tests for the runnable offline scenario application."""

import json
from io import StringIO

import pytest

from atem_ai_vision_mixer.app import main, run_scenario
from atem_ai_vision_mixer.execution.controller import Action, Mode
from atem_ai_vision_mixer.scenarios import load_scenario


def test_run_scenario_prints_one_json_decision_per_step() -> None:
    """Verify the replay output is complete JSONL suitable for later comparison."""
    output = StringIO()

    decisions = run_scenario("sermon", output=output)
    records = [json.loads(line) for line in output.getvalue().splitlines()]

    assert len(decisions) == len(load_scenario("sermon"))
    assert len(records) == len(decisions)
    assert records[0] == decisions[0].model_dump(mode="json")
    assert set(records[0]) == {
        "action",
        "camera",
        "reason",
        "effective_mode",
        "timestamp",
    }


def test_cli_defaults_to_shadow_mode(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify omitting --mode preserves the safest zero-write operating level."""
    exit_code = main(["--scenario", "never_pauses"])
    captured = capsys.readouterr()
    records = [json.loads(line) for line in captured.out.splitlines()]

    assert exit_code == 0
    assert records
    assert {record["effective_mode"] for record in records} == {"shadow"}


def test_sermon_auto_holds_for_a_breath_and_cuts_on_sentence_boundaries() -> None:
    """Verify the normal replay visibly demonstrates its core switching promise."""
    decisions = run_scenario("sermon", mode=Mode.AUTO, output=StringIO())
    reasons = [decision.reason for decision in decisions]
    cuts = [decision for decision in decisions if decision.action is Action.CUT]

    assert "pause_too_short" in reasons
    assert "min_dwell" in reasons
    assert "speaking" in reasons
    assert [decision.camera for decision in cuts] == [2, 3]


def test_camera_failure_replay_moves_program_to_a_healthy_feed() -> None:
    """Verify the scripted failure produces a safe cut away from the broken feed."""
    decisions = run_scenario("camera_fails", mode="auto", output=StringIO())
    cuts = [decision for decision in decisions if decision.action is Action.CUT]

    assert len(cuts) == 1
    assert cuts[0].camera == 2


def test_operator_takeover_stays_in_shadow_for_the_full_quiet_period() -> None:
    """Verify a human take prevents further automatic actions until the exact boundary."""
    decisions = run_scenario("operator_takeover", mode="auto", output=StringIO())
    takeover_decisions = [
        decision for decision in decisions if 2.0 <= decision.timestamp < 32.0
    ]

    assert takeover_decisions
    assert all(decision.action is Action.HOLD for decision in takeover_decisions)
    assert all(
        decision.reason == "operator_takeover" for decision in takeover_decisions
    )
    assert all(
        decision.effective_mode is Mode.SHADOW for decision in takeover_decisions
    )
    assert decisions[-1].timestamp == 32.0
    assert decisions[-1].effective_mode is Mode.AUTO


def test_never_pauses_never_promotes_its_staged_shot() -> None:
    """Verify continuous speech and breath pauses cannot produce a cut."""
    decisions = run_scenario("never_pauses", mode="auto", output=StringIO())

    assert any(decision.action is Action.STAGE for decision in decisions)
    assert all(decision.action is not Action.CUT for decision in decisions)
