"""Safety and nonblocking checks across directors, controller, and switcher."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import perf_counter, sleep

import pytest

from atem_ai_vision_mixer.director import async_runner
from atem_ai_vision_mixer.director.async_runner import AsyncRunner
from atem_ai_vision_mixer.director.policy import DEFAULT_POLICY
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


@pytest.fixture
def submitted_requests(monkeypatch):
    """Observe real executor submissions and await them only in test cleanup."""
    futures = []

    class RecordingExecutor(ThreadPoolExecutor):
        def submit(self, *args, **kwargs):
            future = super().submit(*args, **kwargs)
            futures.append(future)
            return future

    monkeypatch.setattr(async_runner, "ThreadPoolExecutor", RecordingExecutor)
    return futures


@pytest.fixture
def make_runner():
    """Close each runner after its bounded test request has been released."""
    runners = []

    def create(initial="previous", timeout=2.0):
        runner = AsyncRunner(initial_result=initial, timeout_seconds=timeout)
        runners.append(runner)
        return runner

    yield create
    for runner in runners:
        runner.close()


@pytest.fixture
def request_clock(monkeypatch):
    """Control deadline ordering without making deadline tests sleep."""
    clock = {"now": 10.0}
    monkeypatch.setattr(async_runner, "monotonic", lambda: clock["now"])
    return clock


@pytest.mark.parametrize("delay", [0.1, 0.3])
def test_model_delay_does_not_block_shadow_ticks_or_queue_requests(
    delay, make_runner, submitted_requests
):
    state = load_scenario(scenario_names()[0])[0].state
    initial = RulesDirector().decide(state)
    switcher = _switcher_for(state)
    controller = Controller(switcher, mode=Mode.SHADOW)
    runner = make_runner(initial)
    entered, release = Event(), Event()
    called = []

    def slow_model():
        called.append("first")
        entered.set()
        sleep(delay)
        assert release.wait(2)
        return initial

    def skipped_model():
        called.append("skipped")
        return initial

    try:
        start = perf_counter()
        assert runner.tick(slow_model) is initial
        launch_elapsed = perf_counter() - start
        assert entered.wait(1)
        elapsed = []
        for _ in range(20):
            start = perf_counter()
            intent = runner.tick(skipped_model)
            controller.tick(state, intent, now=state.timestamp)
            elapsed.append(perf_counter() - start)
            assert intent is initial
        assert called == ["first"]
        assert len(submitted_requests) == 1
        assert switcher.writes == []
        assert max(elapsed + [launch_elapsed]) < 0.05
    finally:
        release.set()
        if submitted_requests:
            submitted_requests[0].result(timeout=2)


@pytest.mark.parametrize("initial", ["previous", DEFAULT_POLICY])
def test_timely_result_is_accepted_even_when_polled_after_deadline(
    initial, make_runner, submitted_requests, request_clock
):
    runner = make_runner(initial)
    completed = object()
    assert runner.tick(lambda: completed) is initial
    submitted_requests[0].result(timeout=1)
    request_clock["now"] = 20.0

    assert runner.tick(lambda: initial) is completed
    submitted_requests[1].result(timeout=1)


def test_timeout_retains_result_skips_busy_calls_and_discards_late_completion(
    make_runner, submitted_requests, request_clock, caplog
):
    runner = make_runner()
    entered, release = Event(), Event()
    skipped = Event()

    def late_model():
        entered.set()
        assert release.wait(2)
        return "expired"

    def next_model():
        skipped.set()
        return "fresh"

    try:
        assert runner.tick(late_model) == "previous"
        assert entered.wait(1)
        request_clock["now"] = 12.0
        for _ in range(3):
            assert runner.tick(next_model) == "previous"
        assert not skipped.is_set()
        assert len(submitted_requests) == 1
        assert len(caplog.records) == 1
        release.set()
        submitted_requests[0].result(timeout=1)

        assert runner.tick(next_model) == "previous"
        submitted_requests[1].result(timeout=1)
        assert runner.tick(lambda: "next") == "fresh"
        submitted_requests[2].result(timeout=1)
        assert len(caplog.records) == 1
    finally:
        release.set()


@pytest.mark.parametrize(("elapsed", "accepted"), [(1.999, True), (2.0, False)])
def test_deadline_uses_completion_time_when_no_tick_observed_the_expiry(
    elapsed, accepted, make_runner, submitted_requests, request_clock, caplog
):
    runner = make_runner()

    def model():
        request_clock["now"] = 10.0 + elapsed
        return "completed"

    runner.tick(model)
    submitted_requests[0].result(timeout=1)
    request_clock["now"] = 20.0

    assert runner.tick(lambda: "next") == ("completed" if accepted else "previous")
    submitted_requests[1].result(timeout=1)
    assert len(caplog.records) == (0 if accepted else 1)


def test_request_error_retains_previous_result_without_logging_response_content(
    make_runner, submitted_requests, caplog
):
    runner = make_runner()

    def malformed_model():
        raise ValueError("synthetic private response sentinel")

    runner.tick(malformed_model)
    with pytest.raises(ValueError):
        submitted_requests[0].result(timeout=1)

    assert runner.tick(lambda: "recovered") == "previous"
    submitted_requests[1].result(timeout=1)
    assert "ValueError" in caplog.text
    assert "synthetic private response sentinel" not in caplog.text
    assert runner.tick(lambda: "next") == "recovered"
    submitted_requests[2].result(timeout=1)


@pytest.mark.parametrize("busy", [False, True])
def test_close_is_nonblocking_idempotent_and_prevents_more_requests(
    busy, make_runner, submitted_requests
):
    runner = make_runner()
    entered, release = Event(), Event()

    def model():
        entered.set()
        assert release.wait(2)
        return "late"

    try:
        if busy:
            runner.tick(model)
            assert entered.wait(1)
        start = perf_counter()
        runner.close()
        runner.close()
        assert perf_counter() - start < 0.05
        with pytest.raises(RuntimeError, match="closed"):
            runner.tick(lambda: "forbidden")
    finally:
        release.set()
        if submitted_requests:
            submitted_requests[0].result(timeout=1)


@pytest.mark.parametrize(
    "timeout", [0.0, -1.0, float("nan"), float("inf"), -float("inf")]
)
def test_runner_requires_a_positive_finite_timeout(timeout):
    with pytest.raises(ValueError, match="positive and finite"):
        AsyncRunner(initial_result=DEFAULT_POLICY, timeout_seconds=timeout)
