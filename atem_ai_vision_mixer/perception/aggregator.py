"""Assemble recorded perception into WorldState without switcher access."""

from __future__ import annotations

from collections.abc import Mapping
from fractions import Fraction
from importlib import import_module
from math import floor, isclose, isfinite
from pathlib import Path
from typing import Self

import numpy as np

from atem_ai_vision_mixer.capture.source import CapturedFrame
from atem_ai_vision_mixer.capture.tiles import TileRectangle, extract_tiles
from atem_ai_vision_mixer.perception.audio_vad import VadState
from atem_ai_vision_mixer.perception.framing import check_framing
from atem_ai_vision_mixer.perception.health import check_health
from atem_ai_vision_mixer.perception.motion import motion_score
from atem_ai_vision_mixer.perception.people import PeopleDetector
from atem_ai_vision_mixer.world_state import (
    AudioState,
    CameraState,
    ProgramState,
    WorldState,
)

VAD_SAMPLE_RATE = 16000  # Convert native capture audio to one supported Silero rate.


class PerceptionPipeline:
    """Own one recording's camera and audio histories, with explicit reset/close."""

    def __init__(
        self,
        tile_config: Mapping[int, TileRectangle] | None,
        *,
        face_model_path: str | Path,
        pose_model_path: str | Path,
        discrete: bool = False,
    ) -> None:
        """Configure capture geometry and local models; do not read any hardware."""
        if not discrete and not tile_config:
            raise ValueError("Multiview perception requires configured tiles")
        self._tile_config = dict(tile_config) if tile_config is not None else None
        self._discrete = discrete
        self._people = PeopleDetector(face_model_path, pose_model_path)
        self._vad = VadState(VAD_SAMPLE_RATE)
        self._closed = False
        self.reset()

    def tick(
        self,
        captured_frame: CapturedFrame,
        switcher_state: ProgramState,
        *,
        challenger_camera: int | None = None,
    ) -> WorldState:
        """Return a validated snapshot using only audio ending by this video PTS."""
        if self._closed:
            raise RuntimeError("PerceptionPipeline is closed")
        pts = captured_frame.pts
        if not isfinite(pts) or (self._last_pts is not None and pts <= self._last_pts):
            raise ValueError("Frame timestamp must be finite and strictly increasing")
        program = ProgramState.model_validate(switcher_state).model_copy(deep=True)
        if not isfinite(program.seconds_live) or program.seconds_live < 0:
            raise ValueError("Program live duration must be finite and nonnegative")
        tiles = extract_tiles(
            captured_frame.video, self._tile_config, discrete=self._discrete
        )
        if program.live_camera not in tiles or (
            challenger_camera is not None and challenger_camera not in tiles
        ):
            raise ValueError("Live and challenger cameras must have configured tiles")

        health = {}
        motion = {}
        for camera, tile in tiles.items():
            if not isinstance(tile, np.ndarray) or tile.ndim != 3:
                raise ValueError("Camera tiles require BGR image dimensions")
            previous = self._health.get(camera)
            old_framing = self._framing.get(camera)
            if old_framing is not None and old_framing.frame_size != (
                tile.shape[1],
                tile.shape[0],
            ):
                previous = None
            health[camera] = check_health(tile, previous)
            motion[camera] = motion_score(
                health[camera].thumbnail,
                previous.thumbnail if previous is not None else None,
            )

        # Reconstruct native audio before advancing any model. Metadata stays
        # portable; PyAV handles packed/planar formats and channel layout.
        native_audio = None
        metadata = captured_frame.audio_metadata
        if captured_frame.audio is None:
            if metadata is not None:
                raise ValueError("Audio metadata requires audio samples")
        else:
            if metadata is None:
                raise ValueError("Captured audio requires encoding and timing metadata")
            if not np.isfinite(captured_frame.audio).all():
                raise ValueError("Captured audio samples must be finite")
            av = import_module("av")
            native_audio = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(captured_frame.audio),
                format=metadata.sample_format,
                layout=metadata.layout,
            )
            if native_audio.samples != metadata.samples:
                raise ValueError("Captured audio sample count does not match metadata")
            native_audio.sample_rate = metadata.sample_rate
            native_audio.time_base = Fraction(1, metadata.sample_rate)

        try:
            observations = self._people.detect(
                tiles,
                pts,
                live_camera=program.live_camera,
                challenger_camera=challenger_camera,
            )
            framing = {}
            cameras = {}
            for camera, tile in tiles.items():
                framing[camera] = check_framing(
                    observations[camera],
                    frame_width=tile.shape[1],
                    frame_height=tile.shape[0],
                    pts=pts,
                    previous=self._framing.get(camera),
                )
                cameras[camera] = CameraState(
                    feed_healthy=health[camera].feed_healthy,
                    subject_present=observations[camera].subject_present,
                    framing_score=framing[camera].framing_score,
                    motion_score=motion[camera],
                    defects=list(
                        dict.fromkeys(
                            (*health[camera].defects, *framing[camera].defects)
                        )
                    ),
                )
            if native_audio is not None:
                encoding = (
                    metadata.sample_rate,
                    metadata.sample_format,
                    metadata.layout,
                )
                if self._encoding != encoding or not isclose(
                    metadata.pts,
                    self._audio_end,
                    rel_tol=0.0,
                    abs_tol=0.5 / metadata.sample_rate,
                ):
                    self._reset_audio()
                    av = import_module("av")
                    self._resampler = av.AudioResampler(
                        format="fltp", layout="mono", rate=VAD_SAMPLE_RATE
                    )
                    self._fifo = av.AudioFifo()
                    self._audio_origin = metadata.pts
                    self._encoding = encoding
                native_audio.pts = round(
                    (metadata.pts - self._audio_origin) * metadata.sample_rate
                )
                for converted in self._resampler.resample(native_audio):
                    self._fifo.write(converted)
                self._audio_end = metadata.end_pts

            if self._audio_end is not None:
                if pts > self._audio_end + 0.5 / VAD_SAMPLE_RATE:
                    # Missing audio is not silence and must not manufacture a
                    # pause long enough to permit a controller cut.
                    self._reset_audio()
                else:
                    available_samples = floor(
                        (pts - self._audio_origin) * VAD_SAMPLE_RATE
                    )
                    while (
                        self._fifo.samples >= self._vad.block_samples
                        and self._fifo.samples_read + self._vad.block_samples
                        <= available_samples
                    ):
                        block = (
                            self._fifo.read(self._vad.block_samples)
                            .to_ndarray()
                            .reshape(-1)
                        )
                        if not np.isfinite(block).all():
                            raise ValueError("Resampled audio must be finite")
                        # Resampling may slightly overshoot full-scale PCM.
                        block = np.clip(block, -1.0, 1.0).astype(np.float32)
                        self._audio_state = self._vad.process(block)
            result = WorldState(
                timestamp=pts,
                program=program,
                cameras=cameras,
                audio=self._audio_state.model_copy(deep=True),
            )
        except Exception:
            # Some models may already have advanced. Never mix partial state
            # into a later snapshot; the caller can reset/replay the recording.
            self.reset()
            raise

        self._health = health
        self._framing = framing
        self._last_pts = pts
        return result

    def _reset_audio(self) -> None:
        self._vad.reset()
        self._audio_state = AudioState(speaking=False, pause_ms=0, level=0.0)
        self._encoding = None
        self._resampler = None
        self._fifo = None
        self._audio_origin = None
        self._audio_end = None

    def reset(self) -> None:
        """Restart all histories before a seek, another recording, or a replay."""
        if self._closed:
            raise RuntimeError("PerceptionPipeline is closed")
        self._people.reset()
        self._health = {}
        self._framing = {}
        self._last_pts = None
        self._reset_audio()

    def close(self) -> None:
        """Release owned native model resources and stop further ticks."""
        if self._closed:
            return
        try:
            self.reset()
        finally:
            self._closed = True
            self._people.close()
            self._vad = None

    def __enter__(self) -> Self:
        """Keep model and history lifetimes within a with block."""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Release resources even when replay fails."""
        self.close()
