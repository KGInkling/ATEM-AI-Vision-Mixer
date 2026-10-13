"""Safety and nonblocking checks across directors, controller, and switcher."""

import json
import sys
import warnings
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import perf_counter, sleep
from types import SimpleNamespace

import pytest

from atem_ai_vision_mixer.app import run_scenario
from atem_ai_vision_mixer.director import async_runner, cloud_llm, layered
from atem_ai_vision_mixer.director.async_runner import AsyncRunner
from atem_ai_vision_mixer.director.cloud_llm import CloudPolicyDirector
from atem_ai_vision_mixer.director.layered import LayeredDirector
from atem_ai_vision_mixer.director.local_llm import LocalLLMDirector
from atem_ai_vision_mixer.director.policy import DEFAULT_POLICY, CutTempo, ShotPolicy
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
from tools import evaluate_directors as evaluation

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


def test_poll_collects_results_without_submitting_more_requests(
    make_runner, submitted_requests
):
    runner = make_runner()
    assert runner.poll() == "previous"
    assert submitted_requests == []
    runner.tick(lambda: "completed")
    submitted_requests[0].result(timeout=1)
    for _ in range(3):
        assert runner.poll() == "completed"
    assert len(submitted_requests) == 1
    runner.close()
    with pytest.raises(RuntimeError, match="closed"):
        runner.poll()


def test_poll_retains_expired_result_without_queuing_a_retry(
    make_runner, submitted_requests, request_clock
):
    runner = make_runner()
    entered, release = Event(), Event()

    def delayed():
        entered.set()
        assert release.wait(2)
        return "expired"

    try:
        runner.tick(delayed)
        assert entered.wait(1)
        request_clock["now"] = 12.0
        assert runner.poll() == "previous"
        release.set()
        submitted_requests[0].result(timeout=1)
        assert runner.poll() == "previous"
        assert len(submitted_requests) == 1
    finally:
        release.set()


@pytest.fixture
def local_state():
    """Give rules camera two while a model can validly suggest camera one."""
    return WorldState.model_validate(
        {
            "timestamp": 10.0,
            "program": {"live_camera": 1, "seconds_live": 0.0},
            "audio": {"speaking": False, "pause_ms": 500, "level": 0.2},
            "cameras": {
                1: {
                    "feed_healthy": True,
                    "framing_score": 0.4,
                    "subject_present": True,
                    "motion_score": 0.0,
                },
                2: {
                    "feed_healthy": True,
                    "framing_score": 0.9,
                    "subject_present": True,
                    "motion_score": 0.0,
                },
            },
        }
    )


@pytest.fixture
def local_sdk(monkeypatch):
    """Stub SDK/network calls while using the production worker and request builder."""
    control = SimpleNamespace(configs=[], calls=[], answers=[], closed=0, headers=[])

    class HTTPError(Exception):
        pass

    class ResponseError(Exception):
        pass

    class Client:
        def __init__(self, **kwargs):
            self.config = kwargs
            control.configs.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            control.closed += 1

        def chat(self, **kwargs):
            request = SimpleNamespace(headers={"authorization": "synthetic credential"})
            for hook in self.config["event_hooks"]["request"]:
                hook(request)
            control.headers.append(dict(request.headers))
            control.calls.append(kwargs)
            answer = (
                control.answers.pop(0)
                if control.answers
                else json.dumps(
                    {
                        "camera": kwargs["format"]["properties"]["camera"]["enum"][0],
                        "confidence": 0.8,
                        "reason": "shot_variety",
                    }
                )
            )
            if isinstance(answer, Exception):
                raise answer
            if callable(answer):
                answer = answer()
            return {"message": {"content": answer}}

    control.HTTPError, control.ResponseError = HTTPError, ResponseError
    monkeypatch.setitem(sys.modules, "httpx", SimpleNamespace(HTTPError=HTTPError))
    monkeypatch.setitem(
        sys.modules,
        "ollama",
        SimpleNamespace(Client=Client, ResponseError=ResponseError),
    )
    return control


@pytest.fixture
def local_director(local_sdk, submitted_requests):
    director = LocalLLMDirector()
    yield director
    director.close()


def test_local_request_is_bounded_loopback_schema_constrained_and_validated(
    local_director, local_sdk, local_state, submitted_requests
):
    assert local_director.decide(local_state) == RulesDirector().decide(local_state)
    submitted_requests[0].result(timeout=1)
    intent = local_director.decide(local_state)
    submitted_requests[1].result(timeout=1)

    assert intent.camera == 1
    request = local_sdk.calls[0]
    assert request["model"] == "qwen3:4b"
    assert request["format"]["properties"]["camera"]["enum"] == [1, 2]
    assert request["options"] == {"temperature": 0, "num_predict": 128}
    assert request["keep_alive"] == -1
    assert request["stream"] is False and request["think"] is False
    assert [m["role"] for m in request["messages"]] == ["system", "user"]
    assert (
        json.dumps(request["format"], sort_keys=True)
        in request["messages"][0]["content"]
    )
    assert local_sdk.configs[0]["host"] == "http://127.0.0.1:11434"
    assert local_sdk.configs[0]["timeout"] == 2.0
    assert local_sdk.configs[0]["follow_redirects"] is False
    assert local_sdk.configs[0]["trust_env"] is False
    assert all("authorization" not in headers for headers in local_sdk.headers)
    assert local_sdk.closed == 2


def test_local_prompt_excludes_free_text_and_keeps_the_stable_prefix(
    local_director, local_sdk, local_state, submitted_requests
):
    local_state.cameras[1].defects = [
        "black",
        "off_thirds",
        "synthetic private metadata",
    ]
    local_director.policy = ShotPolicy.model_validate(
        {
            "segment": "sermon",
            "preferred_shot": "tight",
            "cut_tempo": "slow",
            "rationale": "synthetic private rationale",
        }
    )
    local_director.decide(local_state)
    submitted_requests[0].result(timeout=1)
    first = local_sdk.calls[0]["messages"]
    payload = json.loads(first[1]["content"])
    assert payload["policy"] == {
        "segment": "sermon",
        "preferred_shot": "tight",
        "cut_tempo": "slow",
    }
    assert payload["world_state"]["cameras"]["1"]["defects"] == ["black", "off_thirds"]
    assert "synthetic private" not in json.dumps(local_sdk.calls)

    local_state.timestamp = 11.0
    local_state.audio.speaking = True
    local_director.policy = DEFAULT_POLICY
    local_director.decide(local_state)
    submitted_requests[1].result(timeout=1)
    second = local_sdk.calls[1]["messages"]
    assert first[0]["content"] == second[0]["content"]
    assert first[1]["content"] != second[1]["content"]
    assert list(json.loads(second[1]["content"])) == ["policy", "world_state"]


@pytest.mark.parametrize(
    "bad_response",
    [
        "not JSON",
        '{"camera":999,"confidence":0.8,"reason":"hold"}',
        '{"camera":true,"confidence":0.8,"reason":"hold"}',
        '{"camera":1,"confidence":1.5,"reason":"hold"}',
        '{"camera":1,"confidence":0.8,"reason":"invented"}',
        '{"camera":1,"confidence":0.8,"reason":"hold","extra":"private"}',
        None,
    ],
)
def test_local_malformed_output_retains_the_previous_valid_intent(
    bad_response, local_director, local_sdk, local_state, submitted_requests, caplog
):
    local_director.decide(local_state)
    submitted_requests[0].result(timeout=1)
    local_sdk.answers.append(bad_response)
    previous = local_director.decide(local_state)
    with pytest.raises(ValueError):
        submitted_requests[1].result(timeout=1)

    assert local_director.decide(local_state) == previous
    submitted_requests[2].result(timeout=1)
    assert bad_response is None or bad_response not in caplog.text


@pytest.mark.parametrize("failure_kind", ["connection", "http", "response"])
def test_local_outage_uses_current_rules_logs_once_and_recovers(
    failure_kind, local_director, local_sdk, local_state, submitted_requests, caplog
):
    error_type = {
        "connection": ConnectionError,
        "http": local_sdk.HTTPError,
        "response": local_sdk.ResponseError,
    }[failure_kind]
    local_sdk.answers.extend([error_type("private transport detail")] * 2)
    local_director.decide(local_state)
    submitted_requests[0].result(timeout=1)
    assert local_director.decide(local_state) == RulesDirector().decide(local_state)
    submitted_requests[1].result(timeout=1)
    local_state.cameras[1].framing_score = 1.0
    assert local_director.decide(local_state) == RulesDirector().decide(local_state)
    submitted_requests[2].result(timeout=1)
    assert len(caplog.records) == 1
    assert "private transport detail" not in caplog.text
    assert local_director.decide(local_state).camera == 1
    submitted_requests[3].result(timeout=1)
    assert len(caplog.records) == 1
    local_sdk.answers.append(error_type("private second outage"))
    local_director.decide(local_state)
    submitted_requests[4].result(timeout=1)
    assert local_director.decide(local_state) == RulesDirector().decide(local_state)
    submitted_requests[5].result(timeout=1)
    assert len(caplog.records) == 2
    assert "private second outage" not in caplog.text


def test_local_missing_sdk_remains_offline_with_rules(
    local_director, monkeypatch, local_state, submitted_requests, caplog
):
    monkeypatch.setitem(sys.modules, "ollama", None)
    local_director.decide(local_state)
    submitted_requests[0].result(timeout=1)
    assert local_director.decide(local_state) == RulesDirector().decide(local_state)
    submitted_requests[1].result(timeout=1)
    assert len(caplog.records) == 1


def test_local_busy_request_keeps_a_snapshot_and_never_writes_switcher(
    local_director, local_sdk, local_state, submitted_requests
):
    entered, release = Event(), Event()

    def paused_response():
        entered.set()
        assert release.wait(2)
        return '{"camera":1,"confidence":0.8,"reason":"hold"}'

    local_sdk.answers.append(paused_response)
    switcher = _switcher_for(local_state)
    controller = Controller(switcher, mode=Mode.SHADOW)
    try:
        local_director.decide(local_state)
        assert entered.wait(1)
        local_state.timestamp = 20.0
        local_state.cameras[1].defects.append("synthetic private later text")
        local_director.policy = DEFAULT_POLICY.model_copy(
            update={"cut_tempo": CutTempo.FAST}
        )
        start = perf_counter()
        for _ in range(20):
            intent = local_director.decide(local_state)
            controller.tick(local_state, intent, now=local_state.timestamp)
        assert perf_counter() - start < 0.1
        assert len(local_sdk.calls) == len(submitted_requests) == 1
        assert (
            json.loads(local_sdk.calls[0]["messages"][1]["content"])["world_state"][
                "timestamp"
            ]
            == 10.0
        )
        assert "synthetic private later text" not in json.dumps(local_sdk.calls)
        assert switcher.writes == []
    finally:
        release.set()
        submitted_requests[0].result(timeout=1)


def test_local_drops_a_retained_intent_when_its_camera_disappears(
    local_director, local_sdk, local_state, submitted_requests
):
    local_director.decide(local_state)
    submitted_requests[0].result(timeout=1)
    assert local_director.decide(local_state).camera == 1
    submitted_requests[1].result(timeout=1)
    del local_state.cameras[1]
    local_sdk.answers.append('{"camera":2,"confidence":0.8,"reason":"hold"}')
    assert local_director.decide(local_state) == RulesDirector().decide(local_state)
    submitted_requests[2].result(timeout=1)
    assert local_sdk.calls[2]["format"]["properties"]["camera"]["enum"] == [2]
    assert local_director.decide(local_state).camera == 2
    submitted_requests[3].result(timeout=1)


def test_local_returns_detached_intents_and_does_not_mutate_inputs(
    local_director, local_state, submitted_requests
):
    before = local_state.model_dump_json()
    local_director.decide(local_state)
    submitted_requests[0].result(timeout=1)
    intent = local_director.decide(local_state)
    submitted_requests[1].result(timeout=1)
    intent.camera = 999
    assert local_director.decide(local_state).camera == 1
    submitted_requests[2].result(timeout=1)
    assert local_state.model_dump_json() == before


def test_local_empty_camera_set_skips_model_and_close_stops_more_calls(
    local_director, local_sdk, local_state
):
    local_state.cameras.clear()
    assert local_director.decide(local_state) == RulesDirector().decide(local_state)
    assert local_sdk.calls == []
    local_state.cameras[2] = WorldState.model_validate(
        {
            **local_state.model_dump(),
            "cameras": {
                2: {
                    "feed_healthy": True,
                    "framing_score": 0.4,
                    "subject_present": True,
                    "motion_score": 0.0,
                }
            },
        }
    ).cameras[2]
    local_director.close()
    with pytest.raises(RuntimeError, match="closed"):
        local_director.decide(local_state)
    assert local_sdk.calls == []


def test_local_extra_observation_fields_cannot_enter_the_prompt(
    local_director, local_sdk, local_state, submitted_requests
):
    class ExtendedState(WorldState):
        speaker_name: str
        transcript: str
        raw_video: bytes

    extended = ExtendedState.model_validate(
        {
            **local_state.model_dump(),
            "speaker_name": "synthetic private name",
            "transcript": "synthetic private transcript",
            "raw_video": b"synthetic private pixel bytes",
        }
    )
    local_director.decide(extended)
    submitted_requests[0].result(timeout=1)
    payload = json.loads(local_sdk.calls[0]["messages"][1]["content"])
    assert set(payload["world_state"]) == {"timestamp", "program", "audio", "cameras"}
    assert "synthetic private" not in json.dumps(local_sdk.calls)


@pytest.mark.parametrize(
    "value", ["synthetic private text", float("nan"), float("inf")]
)
def test_local_mutated_non_numeric_observation_is_rejected_before_sdk_use(
    value, local_director, local_sdk, local_state
):
    local_state.audio.level = value
    with warnings.catch_warnings(record=True) as captured, pytest.raises(ValueError):
        local_director.decide(local_state)
    assert captured == []
    assert local_sdk.calls == []


def test_local_expired_response_cannot_override_rules(
    local_director, local_sdk, local_state, submitted_requests, request_clock
):
    entered, release = Event(), Event()

    def expired_response():
        entered.set()
        assert release.wait(2)
        return '{"camera":1,"confidence":0.8,"reason":"hold"}'

    local_sdk.answers.append(expired_response)
    try:
        local_director.decide(local_state)
        assert entered.wait(1)
        request_clock["now"] = 20.0
        release.set()
        submitted_requests[0].result(timeout=1)
        assert local_director.decide(local_state) == RulesDirector().decide(local_state)
        submitted_requests[1].result(timeout=1)
        assert local_director.decide(local_state).camera == 1
        submitted_requests[2].result(timeout=1)
    finally:
        release.set()


@pytest.fixture
def cloud_sdk(monkeypatch):
    """Stub AWS transport and parsed responses without credential discovery."""
    control = SimpleNamespace(
        configs=[], calls=[], http_configs=[], answers=[], closed=0
    )

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            control.http_configs.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            control.closed += 1

    class AnthropicBedrock:
        def __init__(self, **kwargs):
            control.configs.append(kwargs)
            self.http_client = kwargs["http_client"]
            self.messages = SimpleNamespace(parse=self.parse)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def parse(self, **kwargs):
            control.calls.append(kwargs)
            answer = (
                control.answers.pop(0)
                if control.answers
                else '{"segment":"unknown","preferred_shot":"tight","cut_tempo":"slow","rationale":"Prefer clear framing."}'
            )
            if isinstance(answer, Exception):
                raise answer
            if callable(answer):
                answer = answer()
            response = SimpleNamespace(
                status_code=200,
                read=lambda: None,
                json=lambda: {
                    "usage": {
                        "input_tokens": 50,
                        "output_tokens": 30,
                        "cache_read_input_tokens": 1024,
                    },
                    "content": [{"text": answer}],
                },
            )
            for hook in self.http_client.kwargs["event_hooks"]["response"]:
                hook(response)
            return SimpleNamespace(
                parsed_output=None
                if answer is None
                else kwargs["output_format"].model_validate_json(answer, strict=True)
            )

    monkeypatch.setitem(sys.modules, "httpx2", SimpleNamespace(Client=Client))
    monkeypatch.setitem(
        sys.modules, "anthropic", SimpleNamespace(AnthropicBedrock=AnthropicBedrock)
    )
    return control


@pytest.fixture
def cloud_director(cloud_sdk, submitted_requests, request_clock, monkeypatch):
    monkeypatch.setattr(cloud_llm, "monotonic", lambda: request_clock["now"])
    director = CloudPolicyDirector(
        aws_region="us-east-1", inference_profile="us.anthropic.claude-sonnet-4-6"
    )
    yield director
    director.close()


def test_cloud_default_completed_policy_and_cadence_are_independent(
    cloud_director, cloud_sdk, local_state, submitted_requests, request_clock, caplog
):
    caplog.set_level("INFO")
    assert cloud_director.update(local_state) is DEFAULT_POLICY
    submitted_requests[0].result(timeout=1)
    policy = cloud_director.update(local_state)
    assert policy.preferred_shot.value == "tight"
    for now in (10.5, 20.0, 34.999):
        request_clock["now"] = now
        assert cloud_director.update(local_state) == policy
    assert len(cloud_sdk.calls) == len(submitted_requests) == 1
    request_clock["now"] = 35.0
    cloud_director.update(local_state)
    submitted_requests[1].result(timeout=1)
    assert len(cloud_sdk.calls) == 2
    assert "input=50 output=30 cache_read=1024" in caplog.text
    request = cloud_sdk.calls[0]
    assert request["model"] == "us.anthropic.claude-sonnet-4-6"
    assert request["output_format"] is ShotPolicy
    assert request["output_config"] == {"effort": "low"}
    assert request["max_tokens"] == 1024
    assert request["system"][-1]["cache_control"] == {"type": "ephemeral"}
    assert cloud_sdk.configs[0]["max_retries"] == 0
    assert cloud_sdk.configs[0]["aws_region"] == "us-east-1"
    assert (
        cloud_sdk.configs[0]["base_url"]
        == "https://bedrock-runtime.us-east-1.amazonaws.com"
    )
    assert cloud_sdk.http_configs[0]["trust_env"] is False
    assert cloud_sdk.http_configs[0]["follow_redirects"] is False
    assert cloud_sdk.http_configs[0]["timeout"] == 10.0
    assert cloud_sdk.closed == 2


def test_cloud_prefix_is_stable_and_private_fields_are_not_forwarded(
    cloud_director, cloud_sdk, local_state, submitted_requests, request_clock
):
    local_state.cameras[1].defects = ["soft", "synthetic private text"]
    cloud_director.update(local_state)
    submitted_requests[0].result(timeout=1)
    first = cloud_sdk.calls[0]
    local_state.timestamp = 22.0
    local_state.audio.pause_ms = 800
    request_clock["now"] = 35.0
    cloud_director.update(local_state)
    submitted_requests[1].result(timeout=1)
    second = cloud_sdk.calls[1]
    assert first["system"] == second["system"]
    assert first["messages"] != second["messages"]
    assert "synthetic private text" not in str(cloud_sdk.calls)
    payload = json.loads(first["messages"][0]["content"])
    assert payload["cameras"]["1"]["defects"] == ["soft"]


@pytest.mark.parametrize(
    "answer",
    [
        "not JSON",
        '{"segment":"invented","preferred_shot":"wide","cut_tempo":"slow","rationale":"short"}',
        '{"segment":"sermon","preferred_shot":"tight","cut_tempo":"slow","rationale":"'
        + "a" * 241
        + '"}',
    ],
)
def test_cloud_validation_retains_policy_and_still_reports_numeric_usage(
    answer,
    cloud_director,
    cloud_sdk,
    local_state,
    submitted_requests,
    request_clock,
    caplog,
):
    caplog.set_level("INFO")
    cloud_director.update(local_state)
    submitted_requests[0].result(timeout=1)
    previous = cloud_director.update(local_state)
    cloud_sdk.answers.append(answer)
    request_clock["now"] = 35.0
    cloud_director.update(local_state)
    with pytest.raises(ValueError):
        submitted_requests[1].result(timeout=1)
    assert cloud_director.update(local_state) == previous
    assert len(cloud_sdk.calls) == 2
    assert sum("Cloud usage" in record.message for record in caplog.records) == 2
    assert answer not in caplog.text


def test_cloud_missing_sdk_retains_default_without_aws_access(
    cloud_director, local_state, submitted_requests, monkeypatch
):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    assert cloud_director.update(local_state) is DEFAULT_POLICY
    with pytest.raises(ImportError):
        submitted_requests[0].result(timeout=1)
    assert cloud_director.update(local_state) is DEFAULT_POLICY


def test_cloud_slow_call_skips_requests_and_discards_expired_policy(
    cloud_director, cloud_sdk, local_state, submitted_requests, request_clock
):
    entered, release = Event(), Event()

    def slow():
        entered.set()
        assert release.wait(2)
        return '{"segment":"sermon","preferred_shot":"tight","cut_tempo":"normal","rationale":"Late result."}'

    cloud_sdk.answers.append(slow)
    try:
        assert cloud_director.update(local_state) is DEFAULT_POLICY
        assert entered.wait(1)
        request_clock["now"] = 35.0
        start = perf_counter()
        for _ in range(20):
            assert cloud_director.update(local_state) is DEFAULT_POLICY
        assert perf_counter() - start < 0.05
        assert len(cloud_sdk.calls) == len(submitted_requests) == 1
        release.set()
        submitted_requests[0].result(timeout=1)
        assert cloud_director.update(local_state) is DEFAULT_POLICY
        assert len(cloud_sdk.calls) == 1
    finally:
        release.set()


@pytest.mark.parametrize(
    ("region", "profile", "interval"),
    [
        ("", "us.anthropic.claude-sonnet-4-6", 25),
        ("us-east-1@invalid.example", "us.anthropic.claude-sonnet-4-6", 25),
        ("us-east-1", "anthropic.claude-opus-5", 25),
        ("us-east-1", "us.anthropic.claude-sonnet-4-6", 0),
        ("us-east-1", "us.anthropic.claude-sonnet-4-6", float("nan")),
    ],
)
def test_cloud_requires_explicit_supported_region_profile_and_slow_cadence(
    region, profile, interval
):
    with pytest.raises(ValueError):
        CloudPolicyDirector(
            aws_region=region, inference_profile=profile, interval_seconds=interval
        )


@pytest.mark.parametrize("answer", [None, RuntimeError("synthetic credential failure")])
def test_cloud_absent_policy_or_transport_failure_retains_default_and_recovers(
    answer,
    cloud_director,
    cloud_sdk,
    local_state,
    submitted_requests,
    request_clock,
    caplog,
):
    cloud_sdk.answers.append(answer)
    cloud_director.update(local_state)
    with pytest.raises((ValueError, RuntimeError)):
        submitted_requests[0].result(timeout=1)
    assert cloud_director.update(local_state) is DEFAULT_POLICY
    assert "synthetic credential failure" not in caplog.text
    assert cloud_sdk.closed == 1
    request_clock["now"] = 35.0
    cloud_director.update(local_state)
    submitted_requests[1].result(timeout=1)
    assert cloud_director.update(local_state).preferred_shot.value == "tight"


@pytest.mark.parametrize(
    "body",
    [
        None,
        {},
        {"usage": []},
        {"usage": {"input_tokens": -1, "output_tokens": 1}},
        {"usage": {"input_tokens": True, "output_tokens": 1}},
        ValueError("private response content"),
    ],
)
def test_cloud_invalid_usage_never_logs_response_content(body, caplog):
    caplog.set_level("INFO")

    def response_json():
        if isinstance(body, Exception):
            raise body
        return body

    cloud_llm._log_usage(SimpleNamespace(read=lambda: None, json=response_json))
    assert caplog.text == ""


@pytest.mark.parametrize("cache_usage", [{}, {"cache_read_input_tokens": None}])
def test_cloud_uncached_usage_logs_zero_reads(cache_usage, caplog):
    caplog.set_level("INFO")
    cloud_llm._log_usage(
        SimpleNamespace(
            read=lambda: None,
            json=lambda: {
                "usage": {"input_tokens": 50, "output_tokens": 30, **cache_usage}
            },
        )
    )
    assert "input=50 output=30 cache_read=0" in caplog.text


def test_cloud_explicit_aws_profile_and_close(
    cloud_sdk, submitted_requests, local_state
):
    director = CloudPolicyDirector(
        aws_region="eu-west-1",
        inference_profile="eu.anthropic.claude-sonnet-4-6",
        aws_profile="offline-rehearsal",
        interval_seconds=20,
    )
    try:
        director.update(local_state)
        submitted_requests[0].result(timeout=1)
        assert cloud_sdk.configs[0]["aws_profile"] == "offline-rehearsal"
        assert (
            cloud_sdk.configs[0]["base_url"]
            == "https://bedrock-runtime.eu-west-1.amazonaws.com"
        )
        assert cloud_sdk.calls[0]["model"] == "eu.anthropic.claude-sonnet-4-6"
    finally:
        director.close()
    director.close()
    with pytest.raises(RuntimeError, match="closed"):
        director.update(local_state)


def test_local_poll_only_collects_actual_policy_without_starting_another_call(
    local_director, local_sdk, local_state, submitted_requests
):
    assert local_director.decide(
        local_state, request_due=False
    ) == RulesDirector().decide(local_state)
    assert submitted_requests == []
    local_director.decide(local_state)
    submitted_requests[0].result(timeout=1)
    cloud_policy = DEFAULT_POLICY.model_copy(update={"rationale": "Cloud guidance."})
    local_director.policy = cloud_policy
    assert local_director.decide(local_state, request_due=False).camera == 1
    assert local_director.policy_used is DEFAULT_POLICY
    assert len(submitted_requests) == len(local_sdk.calls) == 1
    local_director.decide(local_state)
    submitted_requests[1].result(timeout=1)
    assert local_director.decide(local_state, request_due=False).camera == 1
    assert local_director.policy_used is cloud_policy
    assert len(submitted_requests) == 2
    del local_state.cameras[1]
    assert local_director.decide(
        local_state, request_due=False
    ) == RulesDirector().decide(local_state)
    assert local_director.policy_used is None


def test_local_poll_only_outage_reports_rules_and_recovery_policy(
    local_director, local_sdk, local_state, submitted_requests
):
    local_sdk.answers.append(local_sdk.HTTPError("synthetic private outage"))
    local_director.decide(local_state)
    submitted_requests[0].result(timeout=1)
    assert local_director.decide(
        local_state, request_due=False
    ) == RulesDirector().decide(local_state)
    assert local_director.policy_used is None
    local_director.decide(local_state)
    submitted_requests[1].result(timeout=1)
    assert local_director.decide(local_state, request_due=False).camera == 1
    assert local_director.policy_used is DEFAULT_POLICY


@pytest.fixture
def make_layered(local_sdk, cloud_sdk, submitted_requests, request_clock, monkeypatch):
    monkeypatch.setattr(layered, "monotonic", lambda: request_clock["now"])
    monkeypatch.setattr(cloud_llm, "monotonic", lambda: request_clock["now"])
    directors = []

    def create(**kwargs):
        director = LayeredDirector(**kwargs)
        directors.append(director)
        return director

    yield create
    for director in directors:
        director.close()


def test_layered_defaults_to_rules_without_importing_or_constructing_model_sdks(
    make_layered, local_sdk, cloud_sdk, local_state, submitted_requests, monkeypatch
):
    for module in ("anthropic", "ollama", "httpx", "httpx2"):
        monkeypatch.setitem(sys.modules, module, None)
    director = make_layered()
    for _ in range(5):
        assert director.decide(local_state) == RulesDirector().decide(local_state)
        assert director.tier == "rules"
    assert submitted_requests == local_sdk.configs == cloud_sdk.configs == []
    director.close()
    director.close()
    with pytest.raises(RuntimeError, match="closed"):
        director.decide(local_state)


def test_layered_local_cadence_collects_between_submissions_and_rechecks_cameras(
    make_layered, local_sdk, local_state, submitted_requests, request_clock
):
    director = make_layered(local_enabled=True)
    assert director.decide(local_state) == RulesDirector().decide(local_state)
    assert director.tier == "rules"
    submitted_requests[0].result(timeout=1)
    assert director.decide(local_state).camera == 1
    assert director.tier == "local"
    for now in (10.1, 12.999):
        request_clock["now"] = now
        assert director.decide(local_state).camera == 1
    assert len(local_sdk.calls) == len(submitted_requests) == 1
    del local_state.cameras[1]
    assert director.decide(local_state).camera == 2
    assert director.tier == "rules"
    request_clock["now"] = 13.0
    director.decide(local_state)
    submitted_requests[1].result(timeout=1)
    assert director.decide(local_state).camera == 2
    assert director.tier == "local"
    assert len(submitted_requests) == 2


def test_layered_cloud_policy_informs_only_the_local_request_that_used_it(
    make_layered, cloud_sdk, local_sdk, local_state, submitted_requests, request_clock
):
    director = make_layered(
        local_enabled=True,
        cloud_enabled=True,
        aws_region="us-east-1",
        inference_profile="us.anthropic.claude-sonnet-4-6",
    )
    director.decide(local_state)
    assert director.tier == "rules"
    for future in submitted_requests[:2]:
        future.result(timeout=1)
    # The first local request used DEFAULT_POLICY before cloud guidance arrived.
    assert director.decide(local_state).camera == 1
    assert director.tier == "local"
    first = json.loads(local_sdk.calls[0]["messages"][1]["content"])
    assert first["policy"]["preferred_shot"] == "wide"
    request_clock["now"] = 13.0
    director.decide(local_state)
    submitted_requests[2].result(timeout=1)
    second = json.loads(local_sdk.calls[1]["messages"][1]["content"])
    assert second["policy"]["preferred_shot"] == "tight"
    assert director.decide(local_state).camera == 1
    assert director.tier == "cloud_informed"
    assert len(cloud_sdk.calls) == 1


def test_layered_cloud_and_local_can_be_disabled_independently(
    make_layered, cloud_sdk, local_sdk, local_state, submitted_requests
):
    director = make_layered(
        cloud_enabled=True,
        aws_region="us-east-1",
        inference_profile="us.anthropic.claude-sonnet-4-6",
    )
    assert director.decide(local_state) == RulesDirector().decide(local_state)
    submitted_requests[0].result(timeout=1)
    assert director.decide(local_state) == RulesDirector().decide(local_state)
    assert director.tier == "rules"
    assert len(cloud_sdk.calls) == 1
    assert local_sdk.calls == []


def test_layered_model_outages_recover_without_relabeling_rules(
    make_layered,
    local_sdk,
    cloud_sdk,
    local_state,
    submitted_requests,
    request_clock,
    caplog,
):
    local_sdk.answers.append(local_sdk.HTTPError("private local failure"))
    cloud_sdk.answers.append(RuntimeError("private cloud failure"))
    director = make_layered(
        local_enabled=True,
        cloud_enabled=True,
        aws_region="us-east-1",
        inference_profile="us.anthropic.claude-sonnet-4-6",
    )
    director.decide(local_state)
    with pytest.raises(RuntimeError):
        submitted_requests[0].result(timeout=1)
    submitted_requests[1].result(timeout=1)
    assert director.decide(local_state) == RulesDirector().decide(local_state)
    assert director.tier == "rules"
    assert "private" not in caplog.text
    request_clock["now"] = 13.0
    director.decide(local_state)
    submitted_requests[2].result(timeout=1)
    director.decide(local_state)
    assert director.tier == "local"


def test_layered_slow_local_keeps_control_ticks_nonblocking_without_queue(
    make_layered, local_sdk, local_state, submitted_requests, request_clock
):
    entered, release = Event(), Event()

    def slow():
        entered.set()
        assert release.wait(2)
        return '{"camera":1,"confidence":0.8,"reason":"hold"}'

    local_sdk.answers.append(slow)
    director = make_layered(local_enabled=True)
    try:
        director.decide(local_state)
        assert entered.wait(1)
        request_clock["now"] = 13.0
        switcher = _switcher_for(local_state)
        controller = Controller(switcher, mode=Mode.SHADOW)
        start = perf_counter()
        for _ in range(20):
            controller.tick(
                local_state, director.decide(local_state), now=local_state.timestamp
            )
        assert perf_counter() - start < 0.05
        assert len(submitted_requests) == len(local_sdk.calls) == 1
        assert switcher.writes == []
        release.set()
        submitted_requests[0].result(timeout=1)
        assert director.decide(local_state) == RulesDirector().decide(local_state)
        assert director.tier == "rules"
        assert len(submitted_requests) == 1
    finally:
        release.set()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"local_interval_seconds": 0},
        {"local_interval_seconds": float("inf")},
        {"cloud_enabled": True},
        {"cloud_enabled": True, "aws_region": "us-east-1"},
    ],
)
def test_layered_rejects_invalid_configuration_before_submitting_requests(
    kwargs, submitted_requests
):
    with pytest.raises(ValueError):
        LayeredDirector(**kwargs)
    assert submitted_requests == []


def test_layered_enabled_models_missing_sdks_still_run_the_whole_app_on_rules(
    make_layered, submitted_requests, monkeypatch
):
    from io import StringIO

    for module in ("anthropic", "ollama", "httpx", "httpx2"):
        monkeypatch.setitem(sys.modules, module, None)
    output = StringIO()
    decisions = run_scenario(
        "sermon",
        output=output,
        local_enabled=True,
        cloud_enabled=True,
        aws_region="us-east-1",
        inference_profile="us.anthropic.claude-sonnet-4-6",
    )
    records = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(decisions) == len(load_scenario("sermon"))
    assert {record["director_tier"] for record in records} == {"rules"}
    assert {record["effective_mode"] for record in records} == {"shadow"}
    assert len(submitted_requests) == 2


@pytest.mark.parametrize(
    "enabled", [(False, False), (True, False), (False, True), (True, True)]
)
def test_layered_directors_never_call_any_switcher_method(
    enabled, make_layered, local_state, submitted_requests, monkeypatch
):
    def forbidden(*args, **kwargs):
        raise AssertionError("Director must not use a switcher")

    for name in (
        "program_input",
        "preview_input",
        "in_transition",
        "set_preview",
        "cut",
        "auto",
        "simulate_operator_take",
    ):
        monkeypatch.setattr(FakeSwitcher, name, forbidden)
    local_enabled, cloud_enabled = enabled
    director = make_layered(
        local_enabled=local_enabled,
        cloud_enabled=cloud_enabled,
        aws_region="us-east-1",
        inference_profile="us.anthropic.claude-sonnet-4-6",
    )
    assert director.decide(local_state).camera in local_state.cameras
    for future in submitted_requests:
        future.result(timeout=1)
    assert director.decide(local_state).camera in local_state.cameras


@pytest.mark.parametrize("scenario_name", scenario_names())
@pytest.mark.parametrize("configuration", ["rules", "local", "cloud_informed"])
def test_each_director_configuration_preserves_safety_for_every_scenario_tick(
    scenario_name,
    configuration,
    make_layered,
    local_sdk,
    submitted_requests,
    request_clock,
):
    director = make_layered(
        local_enabled=configuration != "rules",
        cloud_enabled=configuration == "cloud_informed",
        aws_region="us-east-1",
        inference_profile="us.anthropic.claude-sonnet-4-6",
    )
    steps = load_scenario(scenario_name)
    # Deliberately challenge the live shot; the failed-feed case targets that bad feed.
    local_sdk.answers.extend(
        [
            json.dumps(
                {
                    "camera": 1 if scenario_name == "camera_fails" else 2,
                    "confidence": 0.9,
                    "reason": "better_framing",
                }
            )
        ]
        * len(steps)
    )
    switcher = _switcher_for(steps[0].state)
    shadow_switcher = _switcher_for(steps[0].state)
    config = ControllerConfig()
    controller = Controller(switcher, config=config, mode=Mode.AUTO)
    shadow = Controller(shadow_switcher, mode=Mode.SHADOW)
    commanded_cut_times = []
    tiers = set()
    decisions = []
    awaited = 0
    for step in steps:
        request_clock["now"] = 10.0 + step.state.timestamp
        _apply_operator_take(step, (switcher, shadow_switcher))
        director.decide(step.state)
        # Wait only in the test harness, then poll at this same deterministic tick.
        for future in submitted_requests[awaited:]:
            future.result(timeout=1)
        awaited = len(submitted_requests)
        intent = director.decide(step.state)
        assert intent.camera in step.state.cameras
        tiers.add(director.tier)
        decision = controller.tick(step.state, intent, now=step.state.timestamp)
        decisions.append(decision)
        _assert_auto_safety_properties(
            decision, step.state, config, commanded_cut_times, switcher
        )
        shadow.tick(step.state, intent, now=step.state.timestamp)
        assert shadow_switcher.writes == []
    assert configuration in tiers
    if scenario_name == "camera_fails" and configuration != "rules":
        assert any(decision.reason == "unhealthy_target" for decision in decisions)
    if scenario_name == "never_pauses":
        assert any(decision.action is Action.STAGE for decision in decisions)
        assert all(decision.action is not Action.CUT for decision in decisions)


def test_cloud_hour_of_ticks_bounds_requests_and_reports_each_attempt_usage(
    cloud_director, cloud_sdk, local_state, submitted_requests, request_clock, caplog
):
    caplog.set_level("INFO")
    for elapsed in range(3600):
        request_clock["now"] = 10.0 + elapsed
        cloud_director.update(local_state)
        if elapsed % 25 == 0:
            submitted_requests[-1].result(timeout=1)
    assert len(cloud_sdk.calls) == len(submitted_requests) == 144
    assert (
        sum(
            record.message == "Cloud policy request started."
            for record in caplog.records
        )
        == 144
    )
    assert (
        sum(
            record.message == "Cloud usage input=50 output=30 cache_read=1024"
            for record in caplog.records
        )
        == 144
    )


@pytest.mark.parametrize("empty_cameras", [False, True])
def test_rules_hold_with_no_usable_camera_remains_fail_closed(
    empty_cameras, make_layered, local_state
):
    local_state.program.live_camera = 99
    if empty_cameras:
        local_state.cameras.clear()
    else:
        for camera in local_state.cameras.values():
            camera.feed_healthy = False
    director = make_layered()
    intent = director.decide(local_state)
    assert intent == RulesDirector().decide(local_state)
    assert intent.camera == 99
    assert director.tier == "rules"
    switcher = _switcher_for(local_state)
    decision = Controller(switcher, mode=Mode.AUTO).tick(
        local_state, intent, now=local_state.timestamp
    )
    assert decision.action is Action.HOLD
    assert switcher.writes == []


def test_recorded_evaluation_defaults_to_offline_rules_and_unassessed_quality(
    monkeypatch,
):
    from pathlib import Path

    for module in ("anthropic", "ollama", "httpx", "httpx2"):
        monkeypatch.setitem(sys.modules, module, None)
    result = evaluation.compare_recording(
        Path(__file__).parent / "golden" / "perception.jsonl"
    )
    assert result["snapshots"] == 20
    assert result["span_seconds"] == pytest.approx(1.9)
    assert len(result["input_sha256"]) == 64
    assert set(result["runs"]) == {"rules"}
    assert result["runs"]["rules"]["cuts"] == []
    assert result["runs"]["rules"]["model_evidence"] == "not_requested"
    assert result["quality"] == "unassessed"
    assert result["differences"] == {}


def test_recorded_evaluation_simulates_program_and_dwell_without_mutating_input(
    tmp_path,
):
    states = [step.state for step in load_scenario("sermon")]
    for state in states:
        state.program.live_camera = 1
        state.program.seconds_live = 12.0 + state.timestamp
    path = tmp_path / "states.jsonl"
    path.write_text("\n".join(state.model_dump_json() for state in states) + "\n")
    before = path.read_bytes()
    result = evaluation.compare_recording(path)
    assert [
        (cut["recorded_timestamp"], cut["camera"])
        for cut in result["runs"]["rules"]["cuts"]
    ] == [(3.4, 2), (12.5, 3)]
    assert path.read_bytes() == before
    assert result["runs"]["rules"]["tier_counts"] == {"rules": len(states)}


@pytest.mark.parametrize(
    "rows",
    [
        [],
        ["synthetic private invalid JSON"],
        ['{"timestamp":"synthetic private timestamp"}'],
        [load_scenario("sermon")[0].state.model_dump_json()] * 2,
    ],
)
def test_recorded_evaluation_rejects_invalid_input_without_private_error_text(
    rows, tmp_path
):
    path = tmp_path / "bad.jsonl"
    path.write_text("\n".join(rows))
    with pytest.raises(ValueError) as error:
        evaluation.compare_recording(path)
    assert "synthetic private" not in str(error.value)


def test_recorded_evaluation_input_projection_removes_private_extras(tmp_path):
    state = load_scenario("sermon")[0].state.model_dump(mode="json")
    state["transcript"] = "synthetic private transcript"
    state["cameras"]["1"]["defects"] = ["soft", "synthetic private defect"]
    path = tmp_path / "private.jsonl"
    path.write_text(json.dumps(state) + "\n")
    states, digest = evaluation.load_recording(path)
    assert "synthetic private" not in states[0].model_dump_json()
    assert states[0].cameras[1].defects == ["soft"]
    assert len(digest) == 64


def test_recorded_evaluation_compares_three_real_adapter_paths_with_stubbed_outputs(
    tmp_path, make_layered, submitted_requests, request_clock, monkeypatch
):
    path = tmp_path / "sermon.jsonl"
    path.write_text(
        "\n".join(step.state.model_dump_json() for step in load_scenario("sermon"))
        + "\n"
    )
    monkeypatch.setattr(evaluation, "monotonic", lambda: request_clock["now"])

    def advance(seconds):
        for future in submitted_requests:
            future.result(timeout=1)
        request_clock["now"] += seconds

    monkeypatch.setattr(evaluation, "sleep", advance)
    result = evaluation.compare_recording(
        path,
        local_enabled=True,
        cloud_enabled=True,
        aws_region="us-east-1",
        inference_profile="us.anthropic.claude-sonnet-4-6",
    )
    assert set(result["runs"]) == {"rules", "local", "cloud_informed"}
    assert result["runs"]["local"]["model_evidence"] == "valid_advice"
    assert result["runs"]["cloud_informed"]["model_evidence"] == "valid_advice"
    assert result["runs"]["rules"]["cuts"]
    assert result["runs"]["local"]["cuts"] == []
    assert (
        result["differences"]["local"]["only_rules"] == result["runs"]["rules"]["cuts"]
    )
    assert result["differences"]["local"]["only_variant"] == []
    assert result["quality"] == "unassessed"
    for future in submitted_requests:
        future.result(timeout=1)


def test_recorded_evaluation_missing_sdks_does_not_claim_a_valid_model_comparison(
    tmp_path, make_layered, request_clock, submitted_requests, monkeypatch
):
    for module in ("anthropic", "ollama", "httpx", "httpx2"):
        monkeypatch.setitem(sys.modules, module, None)
    path = tmp_path / "states.jsonl"
    path.write_text(
        "\n".join(step.state.model_dump_json() for step in load_scenario("sermon"))
        + "\n"
    )
    monkeypatch.setattr(evaluation, "monotonic", lambda: request_clock["now"])

    def advance(seconds):
        for future in submitted_requests:
            future.result(timeout=1)
        request_clock["now"] += seconds

    monkeypatch.setattr(evaluation, "sleep", advance)
    result = evaluation.compare_recording(path, local_enabled=True)
    assert result["runs"]["local"]["model_evidence"] == "no_valid_advice"
    assert result["quality"] == "unassessed"
    for future in submitted_requests:
        future.result(timeout=1)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cloud_enabled": True},
        {"local_enabled": True, "cloud_enabled": True},
        {
            "local_enabled": True,
            "cloud_enabled": True,
            "aws_region": "us-east-1",
            "inference_profile": "unsupported",
        },
    ],
)
def test_recorded_evaluation_bad_cloud_configuration_precedes_any_model_submission(
    kwargs, tmp_path, submitted_requests
):
    path = tmp_path / "states.jsonl"
    path.write_text(load_scenario("sermon")[0].state.model_dump_json() + "\n")
    with pytest.raises(ValueError):
        evaluation.compare_recording(path, **kwargs)
    assert submitted_requests == []


def test_recorded_evaluation_preserves_stale_veto_and_closes_on_failure(
    local_state, request_clock, monkeypatch
):
    monkeypatch.setattr(evaluation, "monotonic", lambda: request_clock["now"])
    monkeypatch.setattr(
        evaluation,
        "sleep",
        lambda seconds: request_clock.update(now=request_clock["now"] + seconds),
    )

    class SlowDirector:
        tier = "rules"
        closed = False

        def decide(self, state):
            request_clock["now"] += 1
            return RulesDirector().decide(state)

        def close(self):
            self.closed = True

    states = (
        local_state,
        local_state.model_copy(update={"timestamp": local_state.timestamp + 0.1}),
    )
    director = SlowDirector()
    report = evaluation.replay(states, director, paced=True)
    assert report["stale_snapshots"] == 2
    assert director.closed
    broken = SlowDirector()
    monkeypatch.setattr(
        broken,
        "decide",
        lambda state: (_ for _ in ()).throw(RuntimeError("stub failure")),
    )
    with pytest.raises(RuntimeError, match="stub failure"):
        evaluation.replay(states, broken, paced=False)
    assert broken.closed


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("timestamp", float("nan")),
        ("timestamp", float("inf")),
        ("level", float("inf")),
        ("seconds_live", -1),
    ],
)
def test_recorded_evaluation_rejects_nonfinite_scalars_and_invalid_initial_dwell(
    field, value, tmp_path
):
    state = load_scenario("sermon")[0].state.model_dump(mode="json")
    target = (
        state
        if field == "timestamp"
        else state["audio"]
        if field == "level"
        else state["program"]
    )
    target[field] = value
    path = tmp_path / "bad.jsonl"
    path.write_text(json.dumps(state))
    with pytest.raises(ValueError):
        evaluation.load_recording(path)


def test_recorded_evaluation_cli_outputs_json_and_masks_input_errors(tmp_path, capsys):
    from pathlib import Path

    path = Path(__file__).parent / "golden" / "perception.jsonl"
    assert evaluation.main([str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["quality"] == "unassessed"
    bad = tmp_path / "bad.jsonl"
    bad.write_text("synthetic private invalid row")
    with pytest.raises(SystemExit) as error:
        evaluation.main([str(bad)])
    assert error.value.code == 2
    assert "synthetic private" not in capsys.readouterr().err
    with pytest.raises(SystemExit):
        evaluation.main([str(tmp_path / "missing-private-filename.jsonl")])
    assert "missing-private-filename" not in capsys.readouterr().err


@pytest.mark.parametrize("different", [False, True])
def test_recorded_cut_diff_aligns_snapshot_camera_and_ignores_tier_and_clock_jitter(
    different, tmp_path, request_clock, monkeypatch
):
    path = tmp_path / "states.jsonl"
    path.write_text(
        "\n".join(step.state.model_dump_json() for step in load_scenario("sermon"))
        + "\n"
    )
    monkeypatch.setattr(evaluation, "monotonic", lambda: request_clock["now"])
    monkeypatch.setattr(
        evaluation,
        "sleep",
        lambda seconds: request_clock.update(now=request_clock["now"] + seconds),
    )

    class Advice:
        def __init__(self, **kwargs):
            self.model = kwargs["local_enabled"]
            self.tier = "local" if self.model else "rules"

        def decide(self, state):
            intent = RulesDirector().decide(state)
            if self.model and different:
                intent.camera = 3
            return intent

        def close(self):
            pass

    monkeypatch.setattr(evaluation, "LayeredDirector", Advice)
    result = evaluation.compare_recording(path, local_enabled=True)
    diff = result["differences"]["local"]
    if different:
        assert diff["only_rules"]
        assert diff["only_variant"]
    else:
        assert diff["matching_cuts"] == len(result["runs"]["rules"]["cuts"]) == 2
        assert diff["only_rules"] == diff["only_variant"] == []


def test_recorded_evaluation_cli_opt_ins_and_blank_lines_use_only_stubbed_models(
    tmp_path, make_layered, request_clock, submitted_requests, monkeypatch, capsys
):
    from pathlib import Path

    path = tmp_path / "states.jsonl"
    golden = Path(__file__).parent / "golden" / "perception.jsonl"
    path.write_text("\n" + golden.read_text() + "\n")
    monkeypatch.setattr(evaluation, "monotonic", lambda: request_clock["now"])

    def advance(seconds):
        for future in submitted_requests:
            future.result(timeout=1)
        request_clock["now"] += seconds

    monkeypatch.setattr(evaluation, "sleep", advance)
    assert (
        evaluation.main(
            [
                str(path),
                "--enable-local",
                "--enable-cloud",
                "--aws-region",
                "us-east-1",
                "--inference-profile",
                "us.anthropic.claude-sonnet-4-6",
            ]
        )
        == 0
    )
    report = json.loads(capsys.readouterr().out)
    assert set(report["runs"]) == {"rules", "local", "cloud_informed"}
    assert report["snapshots"] == 20
    assert report["runs"]["cloud_informed"]["model_evidence"] == "no_valid_advice"
    assert report["quality"] == "unassessed"
    for future in submitted_requests:
        future.result(timeout=1)


@pytest.mark.parametrize("duration", [0.0, 1e308])
def test_recorded_evaluation_rejects_overflowing_timeline_before_calls(
    duration, tmp_path
):
    first = load_scenario("sermon")[0].state
    first.timestamp = -1e308
    first.program.seconds_live = duration
    second = first.model_copy(update={"timestamp": 1e308 if duration == 0 else -1e307})
    path = tmp_path / "bad-time.jsonl"
    path.write_text(first.model_dump_json() + "\n" + second.model_dump_json() + "\n")
    with pytest.raises(ValueError):
        evaluation.load_recording(path)
