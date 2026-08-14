"""Scripted world-state timelines for exercising the mixer without hardware."""

from collections.abc import Callable
from dataclasses import dataclass

from atem_ai_vision_mixer.world_state import (
    AudioState,
    CameraState,
    ProgramState,
    WorldState,
)

_FramingScores = tuple[float, float, float]

# Each profile lists cameras 1, 2, and 3 in ATEM source-number order.
_SERMON_OPENING_FRAMING: _FramingScores = (0.45, 0.85, 0.60)
_SERMON_SECOND_SHOT_FRAMING: _FramingScores = (0.55, 0.45, 0.90)
_HEALTHY_OPENING_FRAMING: _FramingScores = (0.80, 0.55, 0.45)
_FAILED_LIVE_CAMERA_FRAMING: _FramingScores = (0.20, 0.85, 0.45)
_CAMERA_TWO_CHALLENGER_FRAMING: _FramingScores = (0.40, 0.85, 0.55)
_CAMERA_THREE_TAKEOVER_FRAMING: _FramingScores = (0.40, 0.50, 0.90)


@dataclass(frozen=True)
class ScenarioStep:
    """Pair one observation with an optional human action at the same time."""

    state: WorldState
    operator_take_camera: int | None = None


def scenario_names() -> tuple[str, ...]:
    """Return stable scenario names for CLI choices and automated replays."""
    return tuple(_SCENARIO_BUILDERS)


def load_scenario(name: str) -> tuple[ScenarioStep, ...]:
    """Build a fresh scripted timeline so one replay cannot mutate another."""
    try:
        build_scenario = _SCENARIO_BUILDERS[name]
    except KeyError as error:
        choices = ", ".join(scenario_names())
        raise ValueError(
            f"Unknown scenario {name!r}. Choose from: {choices}"
        ) from error

    return build_scenario()


def _sermon() -> tuple[ScenarioStep, ...]:
    return (
        _speaking_step(0.0, 1, 12.0, _SERMON_OPENING_FRAMING),
        _speaking_step(0.5, 1, 12.5, _SERMON_OPENING_FRAMING),
        _speaking_step(1.0, 1, 13.0, _SERMON_OPENING_FRAMING),
        _pause_step(2.0, 1, 14.0, _SERMON_OPENING_FRAMING, pause_ms=150),
        _pause_step(3.0, 1, 15.0, _SERMON_OPENING_FRAMING, pause_ms=200),
        _pause_step(3.4, 1, 15.4, _SERMON_OPENING_FRAMING, pause_ms=500),
        _speaking_step(4.0, 2, 0.6, _SERMON_SECOND_SHOT_FRAMING),
        _speaking_step(4.5, 2, 1.1, _SERMON_SECOND_SHOT_FRAMING),
        _speaking_step(5.0, 2, 1.6, _SERMON_SECOND_SHOT_FRAMING),
        _speaking_step(6.0, 2, 2.6, _SERMON_SECOND_SHOT_FRAMING),
        _pause_step(7.1, 2, 3.7, _SERMON_SECOND_SHOT_FRAMING, pause_ms=600),
        _speaking_step(12.0, 2, 8.6, _SERMON_SECOND_SHOT_FRAMING),
        _pause_step(12.5, 2, 9.1, _SERMON_SECOND_SHOT_FRAMING, pause_ms=600),
    )


def _camera_fails() -> tuple[ScenarioStep, ...]:
    return (
        _speaking_step(0.0, 1, 20.0, _HEALTHY_OPENING_FRAMING),
        _speaking_step(
            1.0,
            1,
            21.0,
            _FAILED_LIVE_CAMERA_FRAMING,
            unhealthy_camera=1,
        ),
        _speaking_step(
            1.5,
            1,
            21.5,
            _FAILED_LIVE_CAMERA_FRAMING,
            unhealthy_camera=1,
        ),
        _speaking_step(
            2.0,
            1,
            22.0,
            _FAILED_LIVE_CAMERA_FRAMING,
            unhealthy_camera=1,
        ),
        _pause_step(
            3.0,
            1,
            23.0,
            _FAILED_LIVE_CAMERA_FRAMING,
            pause_ms=200,
            unhealthy_camera=1,
        ),
        _pause_step(
            4.0,
            1,
            24.0,
            _FAILED_LIVE_CAMERA_FRAMING,
            pause_ms=600,
            unhealthy_camera=1,
        ),
        _speaking_step(
            4.5,
            2,
            0.5,
            _FAILED_LIVE_CAMERA_FRAMING,
            unhealthy_camera=1,
        ),
    )


def _operator_takeover() -> tuple[ScenarioStep, ...]:
    return (
        _speaking_step(0.0, 1, 20.0, _CAMERA_TWO_CHALLENGER_FRAMING),
        _speaking_step(0.5, 1, 20.5, _CAMERA_TWO_CHALLENGER_FRAMING),
        _speaking_step(1.0, 1, 21.0, _CAMERA_TWO_CHALLENGER_FRAMING),
        _speaking_step(
            2.0,
            3,
            0.0,
            _CAMERA_THREE_TAKEOVER_FRAMING,
            operator_take_camera=3,
        ),
        _speaking_step(10.0, 3, 8.0, _CAMERA_THREE_TAKEOVER_FRAMING),
        _pause_step(
            31.999,
            3,
            29.999,
            _CAMERA_THREE_TAKEOVER_FRAMING,
            pause_ms=600,
        ),
        _pause_step(32.0, 3, 30.0, _CAMERA_THREE_TAKEOVER_FRAMING, pause_ms=600),
    )


def _never_pauses() -> tuple[ScenarioStep, ...]:
    return (
        _speaking_step(0.0, 1, 20.0, _CAMERA_TWO_CHALLENGER_FRAMING),
        _speaking_step(0.5, 1, 20.5, _CAMERA_TWO_CHALLENGER_FRAMING),
        _speaking_step(1.0, 1, 21.0, _CAMERA_TWO_CHALLENGER_FRAMING),
        _speaking_step(2.0, 1, 22.0, _CAMERA_TWO_CHALLENGER_FRAMING),
        _speaking_step(3.0, 1, 23.0, _CAMERA_TWO_CHALLENGER_FRAMING),
        _pause_step(4.0, 1, 24.0, _CAMERA_TWO_CHALLENGER_FRAMING, pause_ms=150),
        _speaking_step(6.0, 1, 26.0, _CAMERA_TWO_CHALLENGER_FRAMING),
        _pause_step(10.0, 1, 30.0, _CAMERA_TWO_CHALLENGER_FRAMING, pause_ms=250),
        _speaking_step(15.0, 1, 35.0, _CAMERA_TWO_CHALLENGER_FRAMING),
    )


def _speaking_step(
    timestamp: float,
    live_camera: int,
    seconds_live: float,
    framing_scores: _FramingScores,
    *,
    unhealthy_camera: int | None = None,
    operator_take_camera: int | None = None,
) -> ScenarioStep:
    return _step(
        timestamp,
        live_camera,
        seconds_live,
        framing_scores,
        speaking=True,
        pause_ms=0,
        unhealthy_camera=unhealthy_camera,
        operator_take_camera=operator_take_camera,
    )


def _pause_step(
    timestamp: float,
    live_camera: int,
    seconds_live: float,
    framing_scores: _FramingScores,
    *,
    pause_ms: int,
    unhealthy_camera: int | None = None,
) -> ScenarioStep:
    return _step(
        timestamp,
        live_camera,
        seconds_live,
        framing_scores,
        speaking=False,
        pause_ms=pause_ms,
        unhealthy_camera=unhealthy_camera,
    )


def _step(
    timestamp: float,
    live_camera: int,
    seconds_live: float,
    framing_scores: _FramingScores,
    *,
    speaking: bool,
    pause_ms: int,
    unhealthy_camera: int | None = None,
    operator_take_camera: int | None = None,
) -> ScenarioStep:
    cameras = {
        camera_id: CameraState(
            feed_healthy=camera_id != unhealthy_camera,
            framing_score=framing_score,
            subject_present=True,
            motion_score=0.1,
        )
        for camera_id, framing_score in enumerate(framing_scores, start=1)
    }

    return ScenarioStep(
        state=WorldState(
            timestamp=timestamp,
            program=ProgramState(
                live_camera=live_camera,
                seconds_live=seconds_live,
            ),
            audio=AudioState(
                speaking=speaking,
                pause_ms=pause_ms,
                level=0.7 if speaking else 0.1,
            ),
            cameras=cameras,
        ),
        operator_take_camera=operator_take_camera,
    )


_SCENARIO_BUILDERS: dict[str, Callable[[], tuple[ScenarioStep, ...]]] = {
    "sermon": _sermon,
    "camera_fails": _camera_fails,
    "operator_takeover": _operator_takeover,
    "never_pauses": _never_pauses,
}
