"""Speech hysteresis, sample timing, and the pinned local ONNX adapter (R7/R8)."""

import builtins
import importlib.util
import os
import socket
import sys
from hashlib import sha256
from importlib.resources import files
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

from atem_ai_vision_mixer.perception import audio_vad
from atem_ai_vision_mixer.perception.audio_vad import VadState, grade_pause


def test_hysteresis_requires_three_decisions_in_both_directions():
    vad = VadState()
    states = [vad.advance(speech) for speech in [True, True, True, False, False, False]]

    assert [state.speaking for state in states] == [
        False,
        False,
        True,
        True,
        True,
        False,
    ]
    assert [state.pause_ms for state in states] == [32, 64, 0, 0, 0, 96]


def test_single_frame_glitches_do_not_change_speech_state():
    vad = VadState()
    assert not any(vad.advance(value).speaking for value in [True, False, True, False])
    for _ in range(3):
        vad.advance(True)
    assert all(vad.advance(value).speaking for value in [False, True, False, True])


def test_pause_is_zero_during_speech_and_monotonic_during_a_pause():
    vad = VadState()
    previous = vad.advance(False)
    sequence = [False] * 10 + [True] * 4 + [False] * 8 + [True, False, True, True, True]
    for speech in sequence:
        current = vad.advance(speech)
        if current.speaking:
            assert current.pause_ms == 0
        elif not previous.speaking:
            assert current.pause_ms >= previous.pause_ms
        previous = current


def test_initial_silence_uses_sample_duration_without_wall_clock():
    vad = VadState()
    for _ in range(100):
        state = vad.advance(False)
    assert not state.speaking
    assert state.pause_ms == 3200


@pytest.mark.parametrize(
    "pause_ms,grade",
    [
        (0, "breath"),
        (249, "breath"),
        (250, "clause"),
        (399, "clause"),
        (400, "sentence"),
        (1200, "sentence"),
        (1201, "long"),
    ],
)
def test_pause_grade_boundaries(pause_ms, grade):
    assert grade_pause(pause_ms) == grade


@pytest.mark.parametrize("pause_ms", [-1, 1.5, True])
def test_pause_grading_rejects_invalid_durations(pause_ms):
    with pytest.raises(ValueError, match="integer"):
        grade_pause(pause_ms)


@pytest.mark.parametrize("sample_rate", [0, 44100, 48000, 16000.0])
def test_rate_must_be_explicitly_supported(sample_rate):
    with pytest.raises(ValueError, match="8000.*16000"):
        VadState(sample_rate=sample_rate)


@pytest.mark.parametrize(
    "speech,level", [(1, 0.0), (True, float("nan")), (True, -0.1), (False, 1.1)]
)
def test_bad_observations_do_not_advance_hysteresis(speech, level):
    vad = VadState()
    vad.advance(True)
    with pytest.raises(ValueError):
        vad.advance(speech, level=level)
    assert not vad.advance(True).speaking
    assert vad.advance(True).speaking


class _Session:
    def __init__(self, probability=0.9):
        self.probability = probability
        self.calls = []

    def run(self, outputs, inputs):
        self.calls.append({name: value.copy() for name, value in inputs.items()})
        return [np.array([[self.probability]], dtype=np.float32), inputs["state"] + 1]


@pytest.mark.parametrize(
    "sample_rate,samples,context", [(8000, 256, 32), (16000, 512, 64)]
)
def test_inference_preserves_recurrent_state_and_context(
    monkeypatch, sample_rate, samples, context
):
    session = _Session()
    loads = []
    monkeypatch.setattr(
        audio_vad, "_load_session", lambda: loads.append(True) or session
    )
    vad = VadState(sample_rate)
    block = np.full(samples, 0.25, dtype=np.float32)
    first = vad.process(block)
    block[:] = 0.5  # Reused capture storage must not corrupt retained context.
    vad.process(block)
    result = vad.process(block)

    assert len(loads) == 1
    assert not first.speaking and result.speaking
    assert result.pause_ms == 0
    assert result.level == pytest.approx(0.5)
    assert session.calls[0]["input"].shape == (1, samples + context)
    assert np.all(session.calls[0]["input"][0, :context] == 0)
    assert np.all(session.calls[1]["input"][0, :context] == 0.25)
    assert np.all(session.calls[0]["state"] == 0)
    assert np.all(session.calls[1]["state"] == 1)
    assert np.all(session.calls[2]["state"] == 2)
    assert session.calls[0]["sr"].dtype == np.int64
    assert session.calls[0]["sr"].item() == sample_rate


def test_reset_clears_timing_and_model_history_but_reuses_session(monkeypatch):
    session = _Session()
    loads = []
    monkeypatch.setattr(
        audio_vad, "_load_session", lambda: loads.append(True) or session
    )
    vad = VadState()
    block = np.full(512, 0.5, dtype=np.float32)
    for _ in range(3):
        vad.process(block)
    vad.reset()
    state = vad.process(block)

    assert not state.speaking
    assert state.pause_ms == 32
    assert len(loads) == 1
    assert np.all(session.calls[-1]["state"] == 0)
    assert np.all(session.calls[-1]["input"][0, :64] == 0)


@pytest.mark.parametrize(
    "block",
    [
        np.zeros(511, dtype=np.float32),
        np.zeros((1, 512), dtype=np.float32),
        np.zeros(512, dtype=np.int16),
        np.full(512, float("nan"), dtype=np.float32),
        np.full(512, float("inf"), dtype=np.float32),
        np.full(512, 1.01, dtype=np.float32),
    ],
)
def test_bad_audio_is_rejected_before_loading_or_mutating_state(monkeypatch, block):
    def unexpected_load():
        pytest.fail("Invalid PCM must not reach the model")

    monkeypatch.setattr(audio_vad, "_load_session", unexpected_load)
    vad = VadState()
    vad.advance(True)
    with pytest.raises(ValueError, match="PCM"):
        vad.process(block)
    assert not vad.advance(True).speaking
    assert vad.advance(True).speaking


@pytest.mark.parametrize("probability", [float("nan"), -0.1, 1.1])
def test_invalid_probability_does_not_commit_recurrent_state(monkeypatch, probability):
    session = _Session(probability)
    monkeypatch.setattr(audio_vad, "_load_session", lambda: session)
    vad = VadState()
    with pytest.raises(ValueError, match="probability"):
        vad.process(np.zeros(512, dtype=np.float32))
    session.probability = 0.9
    assert not vad.process(np.zeros(512, dtype=np.float32)).speaking
    assert np.all(session.calls[-1]["state"] == 0)


def test_each_stream_owns_its_model_history(monkeypatch):
    session = _Session()
    monkeypatch.setattr(audio_vad, "_load_session", lambda: session)
    first, second = VadState(), VadState()
    block = np.zeros(512, dtype=np.float32)
    for _ in range(3):
        first.process(block)
    state = second.process(block)
    assert not state.speaking
    assert np.all(session.calls[-1]["state"] == 0)


def test_local_resource_loader_uses_verified_bytes_and_single_thread_cpu(monkeypatch):
    captured = {}
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "onnxruntime":
            assert os.environ.get("ORT_DISABLE_TELEMETRY") == "1"
        return real_import(name, *args, **kwargs)

    monkeypatch.setenv("ORT_DISABLE_TELEMETRY", "0")
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    def create_session(model, *, sess_options, providers):
        captured.update(model=model, options=sess_options, providers=providers)
        return "session"

    monkeypatch.setitem(
        sys.modules,
        "onnxruntime",
        SimpleNamespace(
            SessionOptions=SimpleNamespace,
            InferenceSession=create_session,
            disable_telemetry_events=lambda: captured.update(telemetry_disabled=True),
        ),
    )

    assert audio_vad._load_session() == "session"
    assert sha256(captured["model"]).hexdigest() == audio_vad.MODEL_SHA256
    assert captured["providers"] == ["CPUExecutionProvider"]
    assert captured["options"].inter_op_num_threads == 1
    assert captured["options"].intra_op_num_threads == 1
    assert captured["telemetry_disabled"]


def test_model_checksum_mismatch_fails_before_runtime_loading(monkeypatch):
    monkeypatch.setattr(audio_vad, "MODEL_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="checksum"):
        audio_vad._load_session()


@pytest.mark.parametrize("sample_rate,samples", [(8000, 256), (16000, 512)])
def test_packaged_model_runs_offline_and_replays_deterministically(
    monkeypatch, sample_rate, samples
):
    if importlib.util.find_spec("onnxruntime") is None:
        pytest.skip("Real model inference requires the perception extra")

    def reject_network(*args, **kwargs):
        pytest.fail("VAD must not access the network")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    vad = VadState(sample_rate)
    silence = np.zeros(samples, dtype=np.float32)
    first = [vad.process(silence) for _ in range(8)]
    vad.reset()
    second = [vad.process(silence) for _ in range(8)]

    assert first == second
    assert all(not state.speaking for state in first)
    assert first[-1].pause_ms == 256


def test_model_provenance_and_license_are_packaged_without_heavy_dependencies():
    models = files("atem_ai_vision_mixer.perception").joinpath("models")
    assert "v6.2.3" in models.joinpath("README.md").read_text()
    assert "MIT License" in models.joinpath("LICENSE").read_text()
    for module in ["silero_vad", "torch", "torchaudio"]:
        assert importlib.util.find_spec(module) is None
