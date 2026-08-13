"""Deterministic safety rules between shot suggestions and the switcher."""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from atem_ai_vision_mixer.director.intent import ShotIntent
from atem_ai_vision_mixer.execution.switcher import SwitcherClient
from atem_ai_vision_mixer.world_state import WorldState

# Cut timestamps stay relevant for one trailing minute.
CUT_RATE_WINDOW_SECONDS = 60.0


@dataclass
class ControllerConfig:
    """Keep every safety threshold together so tuning never changes control flow."""

    min_dwell_seconds: float = 8.0
    cut_cooldown_seconds: float = 2.0
    cut_pause_min_ms: int = 400
    cut_pause_max_ms: int = 1200
    cut_delay_into_pause_ms: int = 100
    preview_review_seconds: float = 2.0
    hysteresis_margin: float = 0.15
    hysteresis_ticks: int = 3
    max_cuts_per_minute: int = 6
    takeover_shadow_seconds: float = 30.0
    stale_world_state_seconds: float = 0.5


class Mode(StrEnum):
    """Define how far the controller may progress toward changing program."""

    SHADOW = "shadow"
    ASSIST = "assist"
    AUTO = "auto"


class Action(StrEnum):
    """Describe the observable result of one controller tick."""

    HOLD = "hold"
    STAGE = "stage"
    CUT = "cut"


class Decision(BaseModel):
    """Record every controller outcome for logs and later operator comparison."""

    model_config = ConfigDict(extra="forbid")

    action: Action
    camera: int | None
    reason: str
    effective_mode: Mode
    timestamp: float


class Controller:
    """Apply deterministic safety gates before any switcher write is allowed."""

    def __init__(
        self,
        switcher: SwitcherClient,
        config: ControllerConfig | None = None,
        mode: Mode | str = Mode.SHADOW,
    ) -> None:
        """Start from the real program bus so the first tick cannot invent a takeover."""
        self.switcher = switcher
        self.config = config if config is not None else ControllerConfig()
        self.mode = Mode(mode)

        self.staged_camera: int | None = None
        self.staged_at: float | None = None
        self.last_commanded_program = switcher.program_input()
        self.cut_times: list[float] = []
        self.challenger_camera: int | None = None
        self.challenger_ticks = 0
        self.takeover_until: float | None = None

    def tick(
        self,
        state: WorldState,
        intent: ShotIntent,
        now: float,
    ) -> Decision:
        """Return one safe decision using only the supplied monotonic timestamp."""
        self._prune_cut_times(now)
        effective_mode = self._effective_mode(now)

        # Phase 1: these guards apply before both staging and promotion.
        if now - state.timestamp > self.config.stale_world_state_seconds:
            self._clear_selection_state()
            return self._hold("stale_world_state", effective_mode, now)

        takeover_detected = self._record_takeover_if_needed(now)
        takeover_active = self._takeover_is_active(now)
        if takeover_detected or takeover_active:
            self._clear_selection_state()
            return self._hold("operator_takeover", Mode.SHADOW, now)

        if intent.camera not in state.cameras:
            self._clear_selection_state()
            return self._hold("unknown_camera", effective_mode, now)

        target_state = state.cameras[intent.camera]
        if not target_state.feed_healthy:
            self._clear_selection_state()
            return self._hold("unhealthy_target", effective_mode, now)

        if self.staged_camera is not None and intent.camera != self.staged_camera:
            self._clear_selection_state()
            return self._hold("intent_changed", effective_mode, now)

        # Phase 2a: speech does not prevent lining up a future shot on preview.
        if self.staged_camera is None:
            return self._consider_staging(state, intent, now, effective_mode)

        # Phase 2b: an existing stage must pass every promotion rule again.
        return self._consider_promotion(state, intent, now, effective_mode)

    def _consider_staging(
        self,
        state: WorldState,
        intent: ShotIntent,
        now: float,
        effective_mode: Mode,
    ) -> Decision:
        target_camera = intent.camera
        live_camera = state.program.live_camera

        if target_camera == live_camera:
            self._reset_challenger()
            return self._hold("already_live", effective_mode, now)

        if not self._hysteresis_margin_is_met(state, target_camera):
            self._reset_challenger()
            return self._hold("insufficient_margin", effective_mode, now)

        if self.challenger_camera != target_camera:
            self.challenger_camera = target_camera
            self.challenger_ticks = 0

        self.challenger_ticks += 1
        if self.challenger_ticks < self.config.hysteresis_ticks:
            return self._hold("building_confidence", effective_mode, now)

        if effective_mode is not Mode.SHADOW:
            self.switcher.set_preview(target_camera)

        self.staged_camera = target_camera
        self.staged_at = now
        self._reset_challenger()

        return Decision(
            action=Action.STAGE,
            camera=target_camera,
            reason=intent.reason.value,
            effective_mode=effective_mode,
            timestamp=now,
        )

    def _consider_promotion(
        self,
        state: WorldState,
        intent: ShotIntent,
        now: float,
        effective_mode: Mode,
    ) -> Decision:
        target_camera = self.staged_camera
        staged_at = self.staged_at

        if target_camera is None or staged_at is None:
            self._clear_selection_state()
            return self._hold("no_staged_camera", effective_mode, now)

        if (
            effective_mode is not Mode.SHADOW
            and self.switcher.preview_input() != target_camera
        ):
            self._clear_selection_state()
            return self._hold("preview_veto", effective_mode, now)

        if not self._hysteresis_margin_is_met(state, target_camera):
            self._clear_selection_state()
            return self._hold("insufficient_margin", effective_mode, now)

        if now - staged_at < self.config.preview_review_seconds:
            return self._hold("reviewing", effective_mode, now)

        if state.program.seconds_live < self.config.min_dwell_seconds:
            return self._hold("min_dwell", effective_mode, now)

        if self._inside_cut_cooldown(now):
            return self._hold("cooldown", effective_mode, now)

        if len(self.cut_times) >= self.config.max_cuts_per_minute:
            return self._hold("rate_capped", effective_mode, now)

        pause_block_reason = self._pause_block_reason(state)
        if pause_block_reason is not None:
            return self._hold(pause_block_reason, effective_mode, now)

        if effective_mode is Mode.ASSIST:
            return self._hold("awaiting_operator", effective_mode, now)

        if effective_mode is Mode.AUTO:
            self.switcher.cut()
            self.last_commanded_program = target_camera

        self.cut_times.append(now)
        self._clear_selection_state()

        return Decision(
            action=Action.CUT,
            camera=target_camera,
            reason=intent.reason.value,
            effective_mode=effective_mode,
            timestamp=now,
        )

    def _record_takeover_if_needed(self, now: float) -> bool:
        if self.switcher.in_transition(now):
            return False

        actual_program = self.switcher.program_input()
        if actual_program == self.last_commanded_program:
            return False

        self.last_commanded_program = actual_program
        self.takeover_until = now + self.config.takeover_shadow_seconds
        return True

    def _takeover_is_active(self, now: float) -> bool:
        if self.takeover_until is None:
            return False
        return now < self.takeover_until

    def _effective_mode(self, now: float) -> Mode:
        if self._takeover_is_active(now):
            return Mode.SHADOW
        return self.mode

    def _hysteresis_margin_is_met(
        self,
        state: WorldState,
        target_camera: int,
    ) -> bool:
        live_state = state.cameras.get(state.program.live_camera)
        if live_state is None:
            return False

        target_state = state.cameras[target_camera]
        framing_advantage = target_state.framing_score - live_state.framing_score
        return framing_advantage >= self.config.hysteresis_margin

    def _inside_cut_cooldown(self, now: float) -> bool:
        if not self.cut_times:
            return False

        seconds_since_cut = now - self.cut_times[-1]
        return seconds_since_cut < self.config.cut_cooldown_seconds

    def _pause_block_reason(self, state: WorldState) -> str | None:
        if state.audio.speaking:
            return "speaking"

        pause_ms = state.audio.pause_ms
        if pause_ms < self.config.cut_pause_min_ms:
            return "pause_too_short"

        if pause_ms > self.config.cut_pause_max_ms:
            return None

        if pause_ms < self.config.cut_delay_into_pause_ms:
            return "waiting_for_pause_delay"

        return None

    def _prune_cut_times(self, now: float) -> None:
        self.cut_times = [
            cut_time
            for cut_time in self.cut_times
            if now - cut_time <= CUT_RATE_WINDOW_SECONDS
        ]

    def _clear_selection_state(self) -> None:
        self.staged_camera = None
        self.staged_at = None
        self._reset_challenger()

    def _reset_challenger(self) -> None:
        self.challenger_camera = None
        self.challenger_ticks = 0

    def _hold(self, reason: str, effective_mode: Mode, now: float) -> Decision:
        return Decision(
            action=Action.HOLD,
            camera=None,
            reason=reason,
            effective_mode=effective_mode,
            timestamp=now,
        )
