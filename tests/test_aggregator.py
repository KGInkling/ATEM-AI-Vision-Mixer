"""WorldState assembly, causal audio, and explicit pipeline history (R11)."""

from fractions import Fraction
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

import atem_ai_vision_mixer.perception.aggregator as module
from atem_ai_vision_mixer.capture.source import AudioMetadata, CapturedFrame
from atem_ai_vision_mixer.capture.tiles import TileRectangle
from atem_ai_vision_mixer.perception.aggregator import PerceptionPipeline
from atem_ai_vision_mixer.perception.people import PeopleObservation
from atem_ai_vision_mixer.world_state import ProgramState, WorldState


class AudioFrame:
    def __init__(self, data):
        self.data = data
        self.samples = data.shape[1]
        self.sample_rate = 16000
        self.pts = 0
        self.time_base = Fraction(1, 16000)

    @classmethod
    def from_ndarray(cls, data, *, format, layout):
        if data.ndim != 2:
            raise ValueError("audio shape")
        return cls(data.copy())

    def to_ndarray(self):
        return self.data


class AudioResampler:
    def __init__(self, *, format, layout, rate):
        assert (format, layout, rate) == ("fltp", "mono", 16000)

    def resample(self, frame):
        output = AudioFrame(
            np.mean(frame.data, axis=0, keepdims=True).astype(np.float32)
        )
        output.pts = frame.pts
        return [output]


class AudioFifo:
    def __init__(self):
        self.data = np.zeros((1, 0), dtype=np.float32)
        self.samples_read = 0

    @property
    def samples(self):
        return self.data.shape[1]

    def write(self, frame):
        self.data = np.concatenate([self.data, frame.data], axis=1)

    def read(self, size):
        result = AudioFrame(self.data[:, :size].copy())
        result.pts = self.samples_read
        self.data = self.data[:, size:]
        self.samples_read += size
        return result


class People:
    def __init__(self, face, pose):
        self.calls = []
        self.fail = False
        self.closed = False

    def detect(self, tiles, pts, *, live_camera, challenger_camera=None):
        self.calls.append((tuple(tiles), pts, live_camera, challenger_camera))
        if self.fail:
            raise RuntimeError("people inference failed")
        return {camera: PeopleObservation(False, None) for camera in tiles}

    def reset(self):
        pass

    def close(self):
        self.closed = True


class Session:
    def __init__(self):
        self.calls = 0
        self.speech = False

    def run(self, outputs, inputs):
        self.calls += 1
        return np.array([[float(self.speech)]], dtype=np.float32), inputs["state"]


@pytest.fixture
def setup(monkeypatch):
    backend = SimpleNamespace(
        AudioFrame=AudioFrame, AudioResampler=AudioResampler, AudioFifo=AudioFifo
    )
    session = Session()
    monkeypatch.setattr(module, "PeopleDetector", People)
    monkeypatch.setattr(module, "import_module", lambda name: backend)
    monkeypatch.setattr(
        "atem_ai_vision_mixer.perception.audio_vad._load_session", lambda: session
    )
    return session


def _pipeline(*, discrete=False):
    return PerceptionPipeline(
        None
        if discrete
        else {1: TileRectangle(0, 0, 40, 20), 2: TileRectangle(40, 0, 40, 20)},
        face_model_path="configured-face.tflite",
        pose_model_path="configured-pose.task",
        discrete=discrete,
    )


def _frame(pts, *, audio=None, metadata=None, value=100):
    return CapturedFrame(
        np.full((20, 80, 3), value, dtype=np.uint8), audio, pts, metadata
    )


def _audio(pts, count=1600, *, value=0.0, layout="mono"):
    channels = 1 if layout == "mono" else 2
    return np.full((channels, count), value, dtype=np.float32), AudioMetadata(
        16000, "fltp", layout, pts, count
    )


def _tick(pipeline, frame, *, live=1, seconds_live=2.5, challenger=2):
    return pipeline.tick(
        frame,
        ProgramState(live_camera=live, seconds_live=seconds_live),
        challenger_camera=challenger,
    )


def test_tick_returns_valid_world_state_with_caller_program_and_frame_time(setup):
    with _pipeline() as pipeline:
        state = _tick(pipeline, _frame(12.5))
        assert WorldState.model_validate_json(state.model_dump_json()) == state
        assert state.timestamp == 12.5
        assert state.program == ProgramState(live_camera=1, seconds_live=2.5)
        assert set(state.cameras) == {1, 2}
        assert all(camera.feed_healthy for camera in state.cameras.values())
        assert all(not camera.subject_present for camera in state.cameras.values())
        assert all(
            "subject_missing" in camera.defects for camera in state.cameras.values()
        )
        assert pipeline._people.calls == [((1, 2), 12.5, 1, 2)]


def test_health_and_motion_history_are_separate_per_camera(setup):
    with _pipeline() as pipeline:
        for index in range(3):
            frame = _frame(index / 10)
            frame.video[:, 40:] += index
            state = _tick(pipeline, frame)

    assert not state.cameras[1].feed_healthy
    assert "frozen" in state.cameras[1].defects
    assert state.cameras[1].motion_score == 0.0
    assert state.cameras[2].feed_healthy
    assert state.cameras[2].motion_score > 0.0


def test_black_feed_carries_explanation_and_named_framing_defect(setup):
    with _pipeline() as pipeline:
        state = _tick(pipeline, _frame(0.0, value=0))
    assert all(
        not camera.feed_healthy and "black" in camera.defects
        for camera in state.cameras.values()
    )
    assert all(camera.framing_score == 0.0 for camera in state.cameras.values())


def test_framing_from_detected_subject_is_merged_without_private_geometry(
    setup, monkeypatch
):
    def detect(self, tiles, pts, **selection):
        return {
            camera: PeopleObservation(
                True,
                SimpleNamespace(origin_x=10, origin_y=1, width=8, height=10),
                box_source="face",
            )
            for camera in tiles
        }

    monkeypatch.setattr(People, "detect", detect)
    with _pipeline() as pipeline:
        state = _tick(pipeline, _frame(0.0))
    assert all(camera.subject_present for camera in state.cameras.values())
    assert all(0.0 <= camera.framing_score <= 1.0 for camera in state.cameras.values())
    assert "bounding_box" not in state.model_dump_json()
    assert "history" not in state.model_dump_json()


def test_future_audio_waits_until_complete_blocks_end_before_video_time(setup):
    setup.speech = True
    audio, metadata = _audio(0.0, count=3200, value=0.25)
    with _pipeline() as pipeline:
        first = _tick(pipeline, _frame(0.0, audio=audio, metadata=metadata))
        assert setup.calls == 0
        assert not first.audio.speaking and first.audio.pause_ms == 0
        second = _tick(pipeline, _frame(0.1))
        assert setup.calls == 3
        assert second.audio.speaking and second.audio.pause_ms == 0
        assert second.audio.level == pytest.approx(0.25)
        _tick(pipeline, _frame(0.2))
        assert setup.calls == 6


def test_partial_blocks_are_buffered_without_padding_or_video_based_pause_growth(setup):
    first_audio, first_meta = _audio(0.0, count=200)
    second_audio, second_meta = _audio(200 / 16000, count=400)
    with _pipeline() as pipeline:
        _tick(pipeline, _frame(0.0, audio=first_audio, metadata=first_meta))
        state = _tick(pipeline, _frame(0.032, audio=second_audio, metadata=second_meta))
        assert setup.calls == 1
        assert state.audio.pause_ms == 32
        state = _tick(pipeline, _frame(0.036))
        assert state.audio.pause_ms == 32
        assert setup.calls == 1


def test_audio_block_after_the_video_timestamp_is_not_consumed_even_near_a_boundary(
    setup,
):
    audio, metadata = _audio(0.0)
    with _pipeline() as pipeline:
        _tick(pipeline, _frame(0.0, audio=audio, metadata=metadata))
        state = _tick(pipeline, _frame(0.09599))
        assert setup.calls == 2
        assert state.audio.pause_ms == 64


def test_missing_audio_and_timeline_gap_reset_observed_pause_instead_of_inventing_silence(
    setup,
):
    audio, metadata = _audio(0.0)
    with _pipeline() as pipeline:
        _tick(pipeline, _frame(0.0, audio=audio, metadata=metadata))
        state = _tick(pipeline, _frame(0.064))
        assert state.audio.pause_ms == 64
        missing = _tick(pipeline, _frame(0.2))
        assert missing.audio.pause_ms == 0
        assert not missing.audio.speaking
        resumed_audio, resumed_meta = _audio(0.3)
        resumed = _tick(
            pipeline, _frame(0.3, audio=resumed_audio, metadata=resumed_meta)
        )
        assert resumed.audio.pause_ms == 0


def test_audio_layout_change_starts_new_model_history(setup):
    with _pipeline() as pipeline:
        audio, metadata = _audio(0.0)
        _tick(pipeline, _frame(0.0, audio=audio, metadata=metadata))
        state = _tick(pipeline, _frame(0.064))
        assert state.audio.pause_ms == 64
        audio, metadata = _audio(0.1, layout="stereo")
        state = _tick(pipeline, _frame(0.1, audio=audio, metadata=metadata))
        assert state.audio.pause_ms == 0


def test_audio_conversion_clips_filter_overshoot_and_rejects_nonfinite_samples(setup):
    audio, metadata = _audio(0.0, value=1.01)
    with _pipeline() as pipeline:
        _tick(pipeline, _frame(0.0, audio=audio, metadata=metadata))
        state = _tick(pipeline, _frame(0.064))
        assert state.audio.level == 1.0
        bad, metadata = _audio(0.1, value=float("nan"))
        with pytest.raises(ValueError, match="audio"):
            _tick(pipeline, _frame(0.1, audio=bad, metadata=metadata))


def test_audio_requires_metadata_and_a_matching_sample_count_before_models_advance(
    setup,
):
    with _pipeline() as pipeline:
        for frame in [
            _frame(0.0, audio=np.zeros((1, 512), dtype=np.float32)),
            _frame(
                0.0,
                audio=np.zeros((1, 512), dtype=np.float32),
                metadata=AudioMetadata(16000, "fltp", "mono", 0.0, 100),
            ),
            _frame(0.0, metadata=AudioMetadata(16000, "fltp", "mono", 0.0, 512)),
        ]:
            with pytest.raises(ValueError, match="audio|Audio"):
                _tick(pipeline, frame)
        assert pipeline._people.calls == []


def test_reset_replays_identically_and_resolution_change_resets_camera_history(setup):
    with _pipeline(discrete=True) as pipeline:

        def replay():
            return [
                pipeline.tick(
                    CapturedFrame(
                        {1: np.full((20, 40, 3), 100, dtype=np.uint8)}, None, index / 10
                    ),
                    ProgramState(live_camera=1, seconds_live=index / 10),
                ).model_dump()
                for index in range(3)
            ]

        first = replay()
        pipeline.reset()
        assert replay() == first
        state = pipeline.tick(
            CapturedFrame({1: np.full((40, 80, 3), 100, dtype=np.uint8)}, None, 0.3),
            ProgramState(live_camera=1, seconds_live=0.3),
        )
        assert state.cameras[1].feed_healthy
        assert state.cameras[1].motion_score == 0.0


@pytest.mark.parametrize("pts", [float("nan"), float("inf"), 0.0, -1.0])
def test_invalid_or_nonincreasing_time_preserves_previous_history(setup, pts):
    with _pipeline() as pipeline:
        _tick(pipeline, _frame(0.0))
        with pytest.raises(ValueError, match="timestamp"):
            _tick(pipeline, _frame(pts))
        assert len(pipeline._people.calls) == 1


def test_invalid_live_challenger_or_duration_is_rejected_before_inference(setup):
    with _pipeline() as pipeline:
        for kwargs in [
            {"live": 3},
            {"challenger": 3},
            {"seconds_live": -1},
            {"seconds_live": float("nan")},
        ]:
            with pytest.raises(ValueError):
                _tick(pipeline, _frame(0.0), **kwargs)
        assert pipeline._people.calls == []


def test_pipeline_requires_crop_geometry_and_rejects_malformed_discrete_images(setup):
    with pytest.raises(ValueError, match="configured tiles"):
        PerceptionPipeline(None, face_model_path="face", pose_model_path="pose")
    with _pipeline(discrete=True) as pipeline:
        with pytest.raises(ValueError, match="BGR"):
            pipeline.tick(
                CapturedFrame({1: np.zeros(10, dtype=np.uint8)}, None, 0.0),
                ProgramState(live_camera=1, seconds_live=0.0),
            )
        assert pipeline._people.calls == []


def test_nonfinite_resampler_output_never_reaches_the_vad_and_clears_histories(
    setup, monkeypatch
):
    monkeypatch.setattr(
        AudioResampler,
        "resample",
        lambda self, frame: [
            AudioFrame(np.full((1, 1600), float("nan"), dtype=np.float32))
        ],
    )
    audio, metadata = _audio(0.0)
    with _pipeline() as pipeline:
        _tick(pipeline, _frame(0.0, audio=audio, metadata=metadata))
        with pytest.raises(ValueError, match="Resampled audio"):
            _tick(pipeline, _frame(0.064))
        assert setup.calls == 0
        assert pipeline._health == {}


def test_inference_failure_clears_partial_histories_and_close_releases_resources(setup):
    pipeline = _pipeline()
    _tick(pipeline, _frame(0.0))
    pipeline._people.fail = True
    with pytest.raises(RuntimeError, match="inference failed"):
        _tick(pipeline, _frame(0.1))
    pipeline._people.fail = False
    state = _tick(pipeline, _frame(0.0))
    assert state.cameras[1].feed_healthy
    pipeline.close()
    pipeline.close()
    assert pipeline._people.closed
    with pytest.raises(RuntimeError, match="closed"):
        _tick(pipeline, _frame(0.2))
    with pytest.raises(RuntimeError, match="closed"):
        pipeline.reset()
