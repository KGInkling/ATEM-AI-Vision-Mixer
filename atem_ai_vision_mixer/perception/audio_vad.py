"""Detect speech locally and measure pauses using processed audio duration."""

import os
from hashlib import sha256
from importlib.resources import files
from math import isfinite

import numpy as np

from atem_ai_vision_mixer.world_state import AudioState

MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
BLOCK_MS = 32  # Silero accepts 512 samples at 16 kHz or 256 at 8 kHz.
SPEECH_THRESHOLD = 0.5  # Initial probability threshold, pending room recordings.
ENTER_SPEECH_FRAMES = 3  # Reject isolated positive predictions.
ENTER_PAUSE_FRAMES = 3  # Avoid ending speech on a brief negative prediction.


def grade_pause(pause_ms: int) -> str:
    """Name a nonnegative pause duration using the director's timing categories."""
    if type(pause_ms) is not int or pause_ms < 0:
        raise ValueError("Pause duration must be a nonnegative integer in milliseconds")
    if pause_ms < 250:
        return "breath"
    if pause_ms < 400:
        return "clause"
    if pause_ms <= 1200:
        return "sentence"
    return "long"


class VadState:
    """Own model memory and speech/pause history for one continuous audio stream."""

    def __init__(self, sample_rate: int = 16000) -> None:
        """Select 8000 or 16000 Hz; defer loading the local model until inference."""
        if type(sample_rate) is not int or sample_rate not in (8000, 16000):
            raise ValueError("VAD requires a sample rate of 8000 or 16000 Hz")
        self.sample_rate = sample_rate
        self.block_samples = sample_rate * BLOCK_MS // 1000
        self._context_samples = 64 if sample_rate == 16000 else 32
        self._session = None
        self.reset()

    def reset(self) -> None:
        """Start a new stream's history while retaining an already loaded model."""
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros((1, self._context_samples), dtype=np.float32)
        self._speech_frames = 0
        self._silence_frames = 0
        self._speaking = False
        self._pause_ms = 0

    def advance(self, speech: bool, *, level: float = 0.0) -> AudioState:
        """Apply one 32 ms speech decision, keeping timing testable without a model.

        Use either this decision stream or process() for a given stream, not both.
        A confirmed pause includes the silent blocks used to leave speech.
        """
        if type(speech) is not bool or not isfinite(level) or not 0.0 <= level <= 1.0:
            raise ValueError("Expected a boolean speech decision and a level in [0, 1]")

        if speech:
            self._speech_frames = min(self._speech_frames + 1, ENTER_SPEECH_FRAMES)
            self._silence_frames = 0
        else:
            self._silence_frames = min(self._silence_frames + 1, ENTER_PAUSE_FRAMES)
            self._speech_frames = 0

        if self._speaking:
            if self._silence_frames >= ENTER_PAUSE_FRAMES:
                self._speaking = False
                self._pause_ms = self._silence_frames * BLOCK_MS
        elif self._speech_frames >= ENTER_SPEECH_FRAMES:
            self._speaking = True
            self._pause_ms = 0
        else:
            self._pause_ms += BLOCK_MS

        return AudioState(
            speaking=self._speaking,
            pause_ms=self._pause_ms,
            level=float(level),
        )

    def process(self, samples: np.ndarray) -> AudioState:
        """Consume exactly one mono float32 PCM block in [-1, 1], without resampling.

        Blocks must be continuous and at the configured rate. The caller buffers
        partial blocks and resets on a seek or discontinuity; missing audio is not
        silence. No clock, capture metadata, or network access is used here.
        """
        if (
            samples.dtype != np.float32
            or samples.shape != (self.block_samples,)
            or not np.isfinite(samples).all()
            or np.any(np.abs(samples) > 1.0)
        ):
            raise ValueError(
                f"Expected {self.block_samples} mono float32 PCM samples in [-1, 1]"
            )
        if self._session is None:
            self._session = _load_session()

        model_input = np.concatenate((self._context, samples[np.newaxis, :]), axis=1)
        probability, next_state = self._session.run(
            None,
            {
                "input": model_input,
                "state": self._state,
                "sr": np.array(self.sample_rate, dtype=np.int64),
            },
        )
        probability = float(probability[0, 0])
        if not isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError("VAD model returned an invalid speech probability")

        level = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
        result = self.advance(probability >= SPEECH_THRESHOLD, level=level)
        self._state = next_state
        self._context = model_input[:, -self._context_samples :].copy()
        return result


def _load_session():
    model = files("atem_ai_vision_mixer.perception").joinpath(
        "models", "silero_vad.onnx"
    )
    model_bytes = model.read_bytes()
    if sha256(model_bytes).hexdigest() != MODEL_SHA256:
        raise ValueError(
            "Packaged Silero model checksum does not match the pinned release"
        )

    # New runtime builds initialize telemetry during import, before the API call.
    os.environ["ORT_DISABLE_TELEMETRY"] = "1"
    import onnxruntime

    onnxruntime.disable_telemetry_events()
    options = onnxruntime.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    return onnxruntime.InferenceSession(
        model_bytes, sess_options=options, providers=["CPUExecutionProvider"]
    )
