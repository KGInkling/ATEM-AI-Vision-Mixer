"""Tests for the runnable offline scenario application."""

import json
import logging
from io import StringIO
from types import SimpleNamespace

import pytest

from atem_ai_vision_mixer import app
from atem_ai_vision_mixer.app import main, run_scenario
from atem_ai_vision_mixer.director.rules import RulesDirector
from atem_ai_vision_mixer.execution.controller import Action, Mode
from atem_ai_vision_mixer.scenarios import load_scenario


def test_run_scenario_prints_one_json_decision_per_step() -> None:
    """Verify the replay output is complete JSONL suitable for later comparison."""
    output = StringIO()

    decisions = run_scenario("sermon", output=output)
    records = [json.loads(line) for line in output.getvalue().splitlines()]

    assert len(decisions) == len(load_scenario("sermon"))
    assert len(records) == len(decisions)
    assert records[0] == {
        **decisions[0].model_dump(mode="json"),
        "director_tier": "rules",
    }
    assert set(records[0]) == {
        "action",
        "camera",
        "reason",
        "effective_mode",
        "timestamp",
        "director_tier",
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


@pytest.fixture
def app_director(monkeypatch):
    control = SimpleNamespace(
        configs=[], closed=0, tiers=["rules", "local", "cloud_informed"], seen=[]
    )

    class Director:
        def __init__(self, **kwargs):
            control.configs.append(kwargs)
            self.tier = "rules"

        def decide(self, state):
            self.tier = control.tiers[min(len(control.seen), len(control.tiers) - 1)]
            control.seen.append(state)
            return RulesDirector().decide(state)

        def close(self):
            control.closed += 1

    monkeypatch.setattr(app, "LayeredDirector", Director)
    return control


def test_cli_explicit_model_flags_preserve_shadow_and_pass_explicit_aws_routing(
    app_director, capsys
):
    assert (
        main(
            [
                "--scenario",
                "sermon",
                "--enable-local",
                "--enable-cloud",
                "--aws-region",
                "us-east-1",
                "--inference-profile",
                "us.anthropic.claude-sonnet-4-6",
                "--aws-profile",
                "rehearsal",
            ]
        )
        == 0
    )
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert app_director.configs == [
        {
            "local_enabled": True,
            "cloud_enabled": True,
            "aws_region": "us-east-1",
            "inference_profile": "us.anthropic.claude-sonnet-4-6",
            "aws_profile": "rehearsal",
        }
    ]
    assert {record["effective_mode"] for record in records} == {"shadow"}
    assert [record["director_tier"] for record in records[:3]] == [
        "rules",
        "local",
        "cloud_informed",
    ]
    assert app_director.closed == 1


def test_cli_omitted_model_flags_constructs_only_disabled_tiers(app_director, capsys):
    main(["--scenario", "sermon"])
    capsys.readouterr()
    assert app_director.configs == [
        {
            "local_enabled": False,
            "cloud_enabled": False,
            "aws_region": None,
            "inference_profile": None,
            "aws_profile": None,
        }
    ]
    assert app_director.closed == 1


@pytest.mark.parametrize(
    "flags",
    [
        ["--enable-cloud"],
        ["--enable-cloud", "--aws-region", "us-east-1"],
        ["--enable-cloud", "--inference-profile", "us.anthropic.claude-sonnet-4-6"],
        [
            "--enable-cloud",
            "--aws-region",
            "us-east-1",
            "--inference-profile",
            "unsupported",
        ],
    ],
)
def test_cli_invalid_cloud_configuration_fails_before_model_calls(flags, capsys):
    with pytest.raises(SystemExit) as error:
        main(["--scenario", "sermon", *flags])
    assert error.value.code == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "error:" in captured.err


def test_run_scenario_closes_models_when_jsonl_output_fails(app_director):
    class BrokenOutput:
        def write(self, text):
            raise OSError("Output unavailable")

    with pytest.raises(OSError, match="Output unavailable"):
        run_scenario("sermon", output=BrokenOutput())
    assert app_director.closed == 1


@pytest.fixture
def fresh_logging(monkeypatch):
    """Use a fresh CLI stderr sink and restore the caller's logging configuration."""
    root = logging.getLogger()
    monkeypatch.setattr(root, "handlers", [])
    monkeypatch.setattr(root, "level", logging.WARNING)
    for name in ("atem_ai_vision_mixer.director.cloud_llm", "httpx2"):
        monkeypatch.setattr(logging.getLogger(name), "level", logging.NOTSET)
    yield
    for handler in root.handlers:
        handler.close()


@pytest.mark.parametrize("cloud_enabled", [False, True])
def test_cli_cloud_usage_is_visible_without_vendor_info_or_jsonl_contamination(
    cloud_enabled, fresh_logging, monkeypatch, capsys
):
    # Pytest installs call-phase capture handlers after fixture setup.
    monkeypatch.setattr(logging.getLogger(), "handlers", [])

    def replay(*args, **kwargs):
        logger = logging.getLogger("atem_ai_vision_mixer.director.cloud_llm")
        logger.info("Cloud policy request started.")
        logger.info("Cloud usage input=50 output=30 cache_read=0")
        logging.getLogger("httpx2").info("private vendor informational canary")
        print('{"director_tier":"rules"}')
        return []

    monkeypatch.setattr(app, "run_scenario", replay)
    arguments = ["--scenario", "sermon"]
    if cloud_enabled:
        arguments += [
            "--enable-cloud",
            "--aws-region",
            "us-east-1",
            "--inference-profile",
            "us.anthropic.claude-sonnet-4-6",
        ]
    assert main(arguments) == 0
    captured = capsys.readouterr()
    assert captured.out == '{"director_tier":"rules"}\n'
    assert "private vendor" not in captured.err
    if cloud_enabled:
        assert "Cloud policy request started." in captured.err
        assert "Cloud usage input=50 output=30 cache_read=0" in captured.err
    else:
        assert captured.err == ""
