"""Tests for the deterministic switcher safety controller."""

import json

import pytest
from pydantic import ValidationError

from atem_ai_vision_mixer.director.intent import Reason, ShotIntent
from atem_ai_vision_mixer.execution.controller import (
    Action,
    Controller,
    ControllerConfig,
    Decision,
    Mode,
)
from atem_ai_vision_mixer.execution.fake_switcher import FakeSwitcher
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
) -> CameraState:
    return CameraState(
        feed_healthy=healthy,
        framing_score=framing_score,
        subject_present=True,
        motion_score=0.0,
    )


def _world_state(
    now: float,
    *,
    live_camera: int = 1,
    seconds_live: float = 20.0,
    speaking: bool = False,
    pause_ms: int = 600,
    cameras: dict[int, CameraState] | None = None,
    timestamp: float | None = None,
) -> WorldState:
    if cameras is None:
        cameras = {
            1: _camera(0.5),
            2: _camera(0.9),
            3: _camera(0.8),
        }

    if timestamp is None:
        timestamp = now

    return WorldState(
        timestamp=timestamp,
        program=ProgramState(
            live_camera=live_camera,
            seconds_live=seconds_live,
        ),
        audio=AudioState(
            speaking=speaking,
            pause_ms=pause_ms,
            level=0.2,
        ),
        cameras=cameras,
    )


def _intent(
    camera: int = 2,
    reason: Reason = Reason.BETTER_FRAMING,
) -> ShotIntent:
    return ShotIntent(camera=camera, confidence=0.9, reason=reason)


def _fast_config(**overrides: float) -> ControllerConfig:
    values: dict[str, float] = {
        "hysteresis_ticks": 1,
        "preview_review_seconds": 0.0,
    }
    values.update(overrides)
    return ControllerConfig(**values)


def _controller(
    *,
    mode: Mode | str = Mode.AUTO,
    config: ControllerConfig | None = None,
    program: int = 1,
    preview: int = 3,
) -> tuple[Controller, FakeSwitcher]:
    switcher = FakeSwitcher(program=program, preview=preview)
    controller = Controller(switcher=switcher, config=config, mode=mode)
    return controller, switcher


def _stage(
    controller: Controller,
    *,
    start: float = 10.0,
    intent: ShotIntent | None = None,
    state_options: dict | None = None,
) -> tuple[Decision, float]:
    if intent is None:
        intent = _intent()
    if state_options is None:
        state_options = {}

    decision: Decision | None = None
    stage_time = start
    for tick_number in range(controller.config.hysteresis_ticks):
        stage_time = start + (tick_number * 0.1)
        state = _world_state(stage_time, **state_options)
        decision = controller.tick(state, intent, now=stage_time)

    assert decision is not None
    assert decision.action is Action.STAGE
    return decision, stage_time


def test_controller_config_contains_every_documented_default() -> None:
    """Verify all safety tuning starts from the approved offline values."""
    assert ControllerConfig() == ControllerConfig(
        min_dwell_seconds=8.0,
        cut_cooldown_seconds=2.0,
        cut_pause_min_ms=400,
        cut_pause_max_ms=1200,
        cut_delay_into_pause_ms=100,
        preview_review_seconds=2.0,
        hysteresis_margin=0.15,
        hysteresis_ticks=3,
        max_cuts_per_minute=6,
        takeover_shadow_seconds=30.0,
        stale_world_state_seconds=0.5,
    )


def test_controller_defaults_to_shadow_and_reads_initial_program() -> None:
    """Verify a new controller starts read-only without mistaking startup for takeover."""
    controller, switcher = _controller(mode=Mode.SHADOW, program=4)

    assert controller.mode is Mode.SHADOW
    assert controller.last_commanded_program == 4
    assert switcher.writes == []


def test_controller_accepts_a_string_mode_for_future_cli_input() -> None:
    """Verify the later command-line app can pass a validated mode string directly."""
    controller, _ = _controller(mode="assist")

    assert controller.mode is Mode.ASSIST


def test_decision_serializes_exactly_the_observable_fields() -> None:
    """Verify decisions can become JSONL records without a custom encoder."""
    decision = Decision(
        action=Action.STAGE,
        camera=2,
        reason="better_framing",
        effective_mode=Mode.ASSIST,
        timestamp=12.5,
    )

    serialized = json.loads(decision.model_dump_json())

    assert serialized == {
        "action": "stage",
        "camera": 2,
        "reason": "better_framing",
        "effective_mode": "assist",
        "timestamp": 12.5,
    }

    with pytest.raises(ValidationError):
        Decision(
            action=Action.HOLD,
            camera=None,
            reason="already_live",
            effective_mode=Mode.SHADOW,
            timestamp=12.5,
            extra_field=True,
        )


def test_stale_world_state_holds_and_clears_selection_state() -> None:
    """Verify old perception data cannot preserve a shot for later promotion."""
    controller, switcher = _controller(config=_fast_config())
    controller.staged_camera = 2
    controller.staged_at = 8.0
    controller.challenger_camera = 3
    controller.challenger_ticks = 2

    state = _world_state(now=10.0, timestamp=9.49)
    decision = controller.tick(state, _intent(), now=10.0)

    assert decision.action is Action.HOLD
    assert decision.camera is None
    assert decision.reason == "stale_world_state"
    assert controller.staged_camera is None
    assert controller.staged_at is None
    assert controller.challenger_camera is None
    assert controller.challenger_ticks == 0
    assert switcher.writes == []


def test_world_state_at_stale_boundary_remains_usable() -> None:
    """Verify data exactly at the allowed age does not fail the strict stale guard."""
    controller, _ = _controller(config=_fast_config())
    state = _world_state(now=10.0, timestamp=9.5)

    decision = controller.tick(state, _intent(), now=10.0)

    assert decision.action is Action.STAGE


def test_unknown_target_holds_and_discards_a_staged_shot() -> None:
    """Verify an invented camera can neither stage nor preserve an earlier target."""
    controller, switcher = _controller(config=_fast_config())
    controller.staged_camera = 2
    controller.staged_at = 9.0

    decision = controller.tick(_world_state(10.0), _intent(camera=9), now=10.0)

    assert decision.reason == "unknown_camera"
    assert controller.staged_camera is None
    assert switcher.writes == []


def test_unhealthy_target_holds_without_a_switcher_write() -> None:
    """Verify director confidence cannot override a broken target feed."""
    controller, switcher = _controller(config=_fast_config())
    cameras = {
        1: _camera(0.5),
        2: _camera(1.0, healthy=False),
    }

    decision = controller.tick(
        _world_state(10.0, cameras=cameras),
        _intent(),
        now=10.0,
    )

    assert decision.action is Action.HOLD
    assert decision.reason == "unhealthy_target"
    assert switcher.writes == []


def test_target_already_live_holds_and_resets_challenger() -> None:
    """Verify the controller never stages the camera that is already on program."""
    controller, switcher = _controller(config=_fast_config())
    controller.challenger_camera = 2
    controller.challenger_ticks = 4

    decision = controller.tick(_world_state(10.0), _intent(camera=1), now=10.0)

    assert decision.reason == "already_live"
    assert controller.challenger_camera is None
    assert controller.challenger_ticks == 0
    assert switcher.writes == []


def test_missing_live_camera_observation_fails_closed_on_margin() -> None:
    """Verify hysteresis cannot be claimed when the live shot has no quality score."""
    controller, switcher = _controller(
        config=_fast_config(),
        program=7,
        preview=3,
    )
    cameras = {2: _camera(0.9)}

    decision = controller.tick(
        _world_state(10.0, live_camera=7, cameras=cameras),
        _intent(),
        now=10.0,
    )

    assert decision.reason == "insufficient_margin"
    assert switcher.writes == []


def test_hysteresis_accepts_an_advantage_equal_to_the_margin() -> None:
    """Verify the framing comparison includes its configured lower boundary."""
    controller, switcher = _controller(config=_fast_config(hysteresis_margin=0.25))
    cameras = {
        1: _camera(0.5),
        2: _camera(0.75),
    }

    decision = controller.tick(
        _world_state(10.0, cameras=cameras),
        _intent(),
        now=10.0,
    )

    assert decision.action is Action.STAGE
    assert switcher.writes == ["set_preview:2"]


def test_hysteresis_requires_the_configured_consecutive_ticks() -> None:
    """Verify one strong frame cannot immediately change the preview bus."""
    controller, switcher = _controller()

    first = controller.tick(_world_state(10.0), _intent(), now=10.0)
    second = controller.tick(_world_state(10.1), _intent(), now=10.1)
    third = controller.tick(_world_state(10.2), _intent(), now=10.2)

    assert first.reason == "building_confidence"
    assert second.reason == "building_confidence"
    assert third.action is Action.STAGE
    assert third.camera == 2
    assert third.reason == "better_framing"
    assert controller.staged_camera == 2
    assert controller.staged_at == pytest.approx(10.2)
    assert switcher.writes == ["set_preview:2"]


def test_insufficient_margin_resets_accumulated_confidence() -> None:
    """Verify qualifying ticks must be uninterrupted by a weak comparison."""
    controller, switcher = _controller()
    controller.tick(_world_state(10.0), _intent(), now=10.0)

    weak_cameras = {
        1: _camera(0.5),
        2: _camera(0.64),
    }
    weak = controller.tick(
        _world_state(10.1, cameras=weak_cameras),
        _intent(),
        now=10.1,
    )
    next_strong = controller.tick(_world_state(10.2), _intent(), now=10.2)

    assert weak.reason == "insufficient_margin"
    assert next_strong.reason == "building_confidence"
    assert controller.challenger_ticks == 1
    assert switcher.writes == []


def test_new_challenger_starts_its_own_confidence_count() -> None:
    """Verify consecutive support for one camera cannot transfer to another."""
    controller, switcher = _controller()
    controller.tick(_world_state(10.0), _intent(camera=2), now=10.0)
    controller.tick(_world_state(10.1), _intent(camera=2), now=10.1)

    changed = controller.tick(_world_state(10.2), _intent(camera=3), now=10.2)

    assert changed.reason == "building_confidence"
    assert controller.challenger_camera == 3
    assert controller.challenger_ticks == 1
    assert switcher.writes == []


def test_speaking_does_not_prevent_staging() -> None:
    """Verify preview can be prepared while promotion still waits for silence."""
    controller, switcher = _controller(config=_fast_config())
    state = _world_state(10.0, speaking=True, pause_ms=0)

    decision = controller.tick(state, _intent(), now=10.0)

    assert decision.action is Action.STAGE
    assert switcher.writes == ["set_preview:2"]


def test_review_window_blocks_promotion_until_its_exact_boundary() -> None:
    """Verify a staged camera remains visible for the full human review period."""
    controller, switcher = _controller(config=_fast_config(preview_review_seconds=2.0))
    _, staged_at = _stage(controller)

    reviewing = controller.tick(
        _world_state(staged_at + 1.99),
        _intent(),
        now=staged_at + 1.99,
    )
    promoted = controller.tick(
        _world_state(staged_at + 2.0),
        _intent(),
        now=staged_at + 2.0,
    )

    assert reviewing.reason == "reviewing"
    assert promoted.action is Action.CUT
    assert switcher.writes == ["set_preview:2", "cut"]


def test_preview_change_is_a_human_veto_and_resets_the_stage() -> None:
    """Verify the controller does not fight a person who replaces its preview shot."""
    controller, switcher = _controller(config=_fast_config())
    _stage(controller)
    switcher.set_preview(camera=3)
    writes_before_tick = list(switcher.writes)

    decision = controller.tick(_world_state(12.0), _intent(), now=12.0)

    assert decision.reason == "preview_veto"
    assert controller.staged_camera is None
    assert controller.challenger_ticks == 0
    assert switcher.writes == writes_before_tick


def test_director_change_discards_the_old_stage() -> None:
    """Verify a previous suggestion cannot be promoted after the director changes its mind."""
    controller, switcher = _controller(config=_fast_config())
    _stage(controller)
    writes_before_tick = list(switcher.writes)

    decision = controller.tick(_world_state(12.0), _intent(camera=3), now=12.0)

    assert decision.reason == "intent_changed"
    assert controller.staged_camera is None
    assert switcher.writes == writes_before_tick


def test_unhealthy_staged_camera_is_discarded_before_promotion() -> None:
    """Verify feed health is checked again after a camera reaches preview."""
    controller, switcher = _controller(config=_fast_config())
    _stage(controller)
    writes_before_tick = list(switcher.writes)
    cameras = {
        1: _camera(0.5),
        2: _camera(0.9, healthy=False),
    }

    decision = controller.tick(
        _world_state(12.0, cameras=cameras),
        _intent(),
        now=12.0,
    )

    assert decision.reason == "unhealthy_target"
    assert controller.staged_camera is None
    assert switcher.writes == writes_before_tick


def test_lost_margin_discards_the_staged_camera() -> None:
    """Verify a shot that stops being clearly better must qualify again from zero."""
    controller, switcher = _controller(config=_fast_config())
    _stage(controller)
    writes_before_tick = list(switcher.writes)
    cameras = {
        1: _camera(0.5),
        2: _camera(0.6),
    }

    decision = controller.tick(
        _world_state(12.0, cameras=cameras),
        _intent(),
        now=12.0,
    )

    assert decision.reason == "insufficient_margin"
    assert controller.staged_camera is None
    assert switcher.writes == writes_before_tick


def test_incomplete_internal_stage_fails_closed() -> None:
    """Verify partially restored controller state cannot reach the cut path."""
    controller, switcher = _controller(config=_fast_config())
    controller.staged_camera = 2
    controller.staged_at = None

    decision = controller.tick(_world_state(10.0), _intent(), now=10.0)

    assert decision.reason == "no_staged_camera"
    assert controller.staged_camera is None
    assert switcher.writes == []


def test_minimum_dwell_blocks_cut_but_preserves_the_reviewed_stage() -> None:
    """Verify an on-air shot receives its full minimum duration before promotion."""
    controller, switcher = _controller(config=_fast_config())
    _stage(controller)

    blocked = controller.tick(
        _world_state(12.0, seconds_live=7.99),
        _intent(),
        now=12.0,
    )

    assert blocked.reason == "min_dwell"
    assert controller.staged_camera == 2

    allowed = controller.tick(
        _world_state(12.1, seconds_live=8.0),
        _intent(),
        now=12.1,
    )

    assert allowed.action is Action.CUT
    assert switcher.writes == ["set_preview:2", "cut"]


def test_cut_cooldown_blocks_until_its_exact_boundary() -> None:
    """Verify two controller cuts cannot occur inside the hard cooldown window."""
    config = _fast_config(cut_cooldown_seconds=2.0)
    controller, switcher = _controller(config=config)
    _stage(controller, start=10.0)
    first_cut = controller.tick(_world_state(10.0), _intent(), now=10.0)
    assert first_cut.action is Action.CUT

    reverse_cameras = {
        1: _camera(0.9),
        2: _camera(0.5),
    }
    reverse_options = {
        "live_camera": 2,
        "cameras": reverse_cameras,
    }
    _stage(
        controller,
        start=10.5,
        intent=_intent(camera=1),
        state_options=reverse_options,
    )

    blocked = controller.tick(
        _world_state(11.99, **reverse_options),
        _intent(camera=1),
        now=11.99,
    )
    allowed = controller.tick(
        _world_state(12.0, **reverse_options),
        _intent(camera=1),
        now=12.0,
    )

    assert blocked.reason == "cooldown"
    assert allowed.action is Action.CUT
    assert switcher.writes.count("cut") == 2


def test_rate_cap_keeps_cuts_at_the_exact_sixty_second_boundary() -> None:
    """Verify a cut remains in the trailing window until it is more than a minute old."""
    config = _fast_config(
        cut_cooldown_seconds=0.0,
        max_cuts_per_minute=1,
    )
    controller, switcher = _controller(config=config)
    _stage(controller, start=10.0)
    controller.tick(_world_state(10.0), _intent(), now=10.0)

    reverse_cameras = {
        1: _camera(0.9),
        2: _camera(0.5),
    }
    reverse_options = {
        "live_camera": 2,
        "cameras": reverse_cameras,
    }
    _stage(
        controller,
        start=20.0,
        intent=_intent(camera=1),
        state_options=reverse_options,
    )

    at_boundary = controller.tick(
        _world_state(70.0, **reverse_options),
        _intent(camera=1),
        now=70.0,
    )
    after_boundary = controller.tick(
        _world_state(70.001, **reverse_options),
        _intent(camera=1),
        now=70.001,
    )

    assert at_boundary.reason == "rate_capped"
    assert after_boundary.action is Action.CUT
    assert controller.cut_times == [pytest.approx(70.001)]
    assert switcher.writes.count("cut") == 2


@pytest.mark.parametrize(
    ("speaking", "pause_ms", "expected_action", "expected_reason"),
    [
        (True, 600, Action.HOLD, "speaking"),
        (False, 399, Action.HOLD, "pause_too_short"),
        (False, 400, Action.CUT, "better_framing"),
        (False, 1201, Action.CUT, "better_framing"),
    ],
)
def test_pause_window_controls_only_promotion(
    speaking: bool,
    pause_ms: int,
    expected_action: Action,
    expected_reason: str,
) -> None:
    """Verify speech, breath, sentence-boundary, and extended-silence cases."""
    controller, switcher = _controller(config=_fast_config())
    state_options = {
        "speaking": speaking,
        "pause_ms": pause_ms,
    }
    _stage(controller, state_options=state_options)

    decision = controller.tick(
        _world_state(12.0, **state_options),
        _intent(),
        now=12.0,
    )

    assert decision.action is expected_action
    assert decision.reason == expected_reason
    assert switcher.writes.count("cut") == (1 if expected_action is Action.CUT else 0)


def test_configured_delay_can_hold_inside_the_pause_window() -> None:
    """Verify a cut waits until the configured point inside an eligible short pause."""
    config = _fast_config(
        cut_pause_min_ms=50,
        cut_pause_max_ms=1200,
        cut_delay_into_pause_ms=100,
    )
    controller, switcher = _controller(config=config)
    state_options = {"pause_ms": 99}
    _stage(controller, state_options=state_options)

    decision = controller.tick(
        _world_state(12.0, **state_options),
        _intent(),
        now=12.0,
    )

    assert decision.reason == "waiting_for_pause_delay"
    assert switcher.writes == ["set_preview:2"]


def test_auto_mode_promotes_preview_and_updates_command_tracking() -> None:
    """Verify a safe automatic cut swaps buses and records its pacing state."""
    controller, switcher = _controller(config=_fast_config())
    _stage(controller, start=10.0)

    decision = controller.tick(_world_state(10.0), _intent(), now=10.0)

    assert decision == Decision(
        action=Action.CUT,
        camera=2,
        reason="better_framing",
        effective_mode=Mode.AUTO,
        timestamp=10.0,
    )
    assert switcher.program_input() == 2
    assert switcher.preview_input() == 1
    assert switcher.writes == ["set_preview:2", "cut"]
    assert controller.last_commanded_program == 2
    assert controller.cut_times == [10.0]
    assert controller.staged_camera is None


def test_assist_mode_stages_once_and_waits_for_the_operator() -> None:
    """Verify assist can prepare preview but can never promote it."""
    controller, switcher = _controller(
        mode=Mode.ASSIST,
        config=_fast_config(),
    )
    stage, _ = _stage(controller)

    waiting = controller.tick(_world_state(12.0), _intent(), now=12.0)

    assert stage.action is Action.STAGE
    assert waiting.action is Action.HOLD
    assert waiting.reason == "awaiting_operator"
    assert waiting.effective_mode is Mode.ASSIST
    assert controller.staged_camera == 2
    assert controller.cut_times == []
    assert switcher.writes == ["set_preview:2"]
    assert switcher.program_input() == 1


def test_shadow_mode_simulates_full_decisions_without_writes() -> None:
    """Verify shadow logs proposed stages and cuts while leaving both buses untouched."""
    controller, switcher = _controller(
        mode=Mode.SHADOW,
        config=_fast_config(),
    )

    stage, _ = _stage(controller)
    cut = controller.tick(_world_state(12.0), _intent(), now=12.0)

    assert stage.action is Action.STAGE
    assert stage.effective_mode is Mode.SHADOW
    assert cut.action is Action.CUT
    assert cut.effective_mode is Mode.SHADOW
    assert controller.cut_times == [12.0]
    assert switcher.writes == []
    assert switcher.program_input() == 1
    assert switcher.preview_input() == 3


def test_operator_takeover_immediately_enters_shadow_and_discards_stage() -> None:
    """Verify a human program change stops controller writes on the same tick."""
    controller, switcher = _controller(config=_fast_config())
    _stage(controller)
    switcher.simulate_operator_take(camera=3)
    writes_before_tick = list(switcher.writes)

    decision = controller.tick(
        _world_state(12.0, live_camera=3),
        _intent(),
        now=12.0,
    )

    assert decision.action is Action.HOLD
    assert decision.reason == "operator_takeover"
    assert decision.effective_mode is Mode.SHADOW
    assert controller.takeover_until == 42.0
    assert controller.last_commanded_program == 3
    assert controller.staged_camera is None
    assert switcher.writes == writes_before_tick


def test_further_takeover_extends_shadow_and_exact_boundary_recovers() -> None:
    """Verify each new human take gets a fresh quiet period before auto resumes."""
    controller, switcher = _controller(config=_fast_config())
    switcher.simulate_operator_take(camera=3)
    first = controller.tick(
        _world_state(10.0, live_camera=3),
        _intent(camera=3),
        now=10.0,
    )

    switcher.simulate_operator_take(camera=2)
    second = controller.tick(
        _world_state(20.0, live_camera=2),
        _intent(camera=2),
        now=20.0,
    )
    still_shadow = controller.tick(
        _world_state(49.999, live_camera=2),
        _intent(camera=2),
        now=49.999,
    )
    recovered = controller.tick(
        _world_state(50.0, live_camera=2),
        _intent(camera=2),
        now=50.0,
    )

    assert first.effective_mode is Mode.SHADOW
    assert second.effective_mode is Mode.SHADOW
    assert controller.takeover_until == 50.0
    assert still_shadow.reason == "operator_takeover"
    assert recovered.reason == "already_live"
    assert recovered.effective_mode is Mode.AUTO
    assert switcher.writes == []


def test_controller_owned_cut_is_not_mistaken_for_takeover() -> None:
    """Verify command tracking accepts the program change caused by its own cut."""
    controller, switcher = _controller(config=_fast_config())
    _stage(controller, start=10.0)
    controller.tick(_world_state(10.0), _intent(), now=10.0)

    decision = controller.tick(
        _world_state(11.0, live_camera=2),
        _intent(camera=2),
        now=11.0,
    )

    assert decision.reason == "already_live"
    assert decision.effective_mode is Mode.AUTO
    assert controller.takeover_until is None
    assert switcher.writes == ["set_preview:2", "cut"]


def test_switcher_transition_suppresses_false_takeover_detection() -> None:
    """Verify a controller-expected transition may settle before program is compared."""
    controller, switcher = _controller(config=_fast_config(), preview=2)
    controller.last_commanded_program = 2
    switcher.auto(now=10.0)

    during = controller.tick(
        _world_state(10.5),
        _intent(camera=1),
        now=10.5,
    )
    after = controller.tick(
        _world_state(11.0, live_camera=2),
        _intent(camera=2),
        now=11.0,
    )

    assert during.reason == "already_live"
    assert after.reason == "already_live"
    assert controller.takeover_until is None
    assert switcher.program_input() == 2


def test_identical_controllers_and_inputs_produce_identical_results() -> None:
    """Verify controller decisions depend only on explicit input and prior state."""
    config = ControllerConfig(
        hysteresis_ticks=2,
        preview_review_seconds=1.0,
    )
    first, first_switcher = _controller(mode=Mode.SHADOW, config=config)
    second, second_switcher = _controller(
        mode=Mode.SHADOW,
        config=ControllerConfig(
            hysteresis_ticks=2,
            preview_review_seconds=1.0,
        ),
    )
    timeline = [10.0, 10.1, 10.5, 11.1, 12.0]

    first_decisions = [
        first.tick(_world_state(now), _intent(), now=now) for now in timeline
    ]
    second_decisions = [
        second.tick(_world_state(now), _intent(), now=now) for now in timeline
    ]

    assert first_decisions == second_decisions
    assert first.cut_times == second.cut_times
    assert first_switcher.writes == second_switcher.writes == []
