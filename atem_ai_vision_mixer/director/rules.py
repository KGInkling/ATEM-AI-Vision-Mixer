"""Deterministic camera selection from one validated world-state snapshot."""

from atem_ai_vision_mixer.director.intent import Reason, ShotIntent
from atem_ai_vision_mixer.world_state import CameraState, WorldState

# Framing is the strongest visual-quality signal in the offline world state.
FRAMING_WEIGHT = 0.8

# A visible subject can outweigh a small framing-score disadvantage.
SUBJECT_PRESENT_BONUS = 0.2

# Shot variety can reduce the live camera's score by at most this amount.
MAX_LIVE_STALENESS_PENALTY = 0.2

# The maximum staleness penalty is reached after this many seconds on one shot.
FULL_STALENESS_SECONDS = 30.0

# Holding is fully confident when there is no healthy alternative to consider.
NO_HEALTHY_CAMERA_HOLD_CONFIDENCE = 1.0


class RulesDirector:
    """Choose a camera with explicit scoring so offline behavior is repeatable."""

    def decide(self, state: WorldState) -> ShotIntent:
        """Return the best healthy shot while preferring the live camera on exact ties."""
        scores = self._score_healthy_cameras(state)
        if not scores:
            return ShotIntent(
                camera=state.program.live_camera,
                confidence=NO_HEALTHY_CAMERA_HOLD_CONFIDENCE,
                reason=Reason.HOLD,
            )

        winning_camera = self._select_winning_camera(state, scores)
        reason = self._reason_for_winner(state, winning_camera)
        confidence = _normalized_score(scores[winning_camera])

        return ShotIntent(
            camera=winning_camera,
            confidence=confidence,
            reason=reason,
        )

    def _score_healthy_cameras(self, state: WorldState) -> dict[int, float]:
        scores: dict[int, float] = {}

        for camera_id, camera in state.cameras.items():
            if not camera.feed_healthy:
                continue

            score = _base_camera_score(camera)
            if camera_id == state.program.live_camera:
                score -= _live_staleness_penalty(state.program.seconds_live)

            scores[camera_id] = score

        return scores

    def _select_winning_camera(
        self,
        state: WorldState,
        scores: dict[int, float],
    ) -> int:
        live_camera = state.program.live_camera
        if live_camera in scores:
            winning_camera = live_camera
        else:
            winning_camera = min(scores)

        for camera_id in sorted(scores):
            if scores[camera_id] > scores[winning_camera]:
                winning_camera = camera_id

        return winning_camera

    def _reason_for_winner(self, state: WorldState, winning_camera: int) -> Reason:
        live_camera_id = state.program.live_camera
        if winning_camera == live_camera_id:
            return Reason.HOLD

        winning_camera_state = state.cameras[winning_camera]
        live_camera_state = state.cameras.get(live_camera_id)

        if winning_camera_state.subject_present and (
            live_camera_state is None or not live_camera_state.subject_present
        ):
            return Reason.SUBJECT_PRESENT

        if (
            live_camera_state is None
            or winning_camera_state.framing_score > live_camera_state.framing_score
        ):
            return Reason.BETTER_FRAMING

        return Reason.SHOT_VARIETY


def _base_camera_score(camera: CameraState) -> float:
    score = camera.framing_score * FRAMING_WEIGHT
    if camera.subject_present:
        score += SUBJECT_PRESENT_BONUS
    return score


def _live_staleness_penalty(seconds_live: float) -> float:
    non_negative_seconds = max(seconds_live, 0.0)
    staleness_fraction = min(non_negative_seconds / FULL_STALENESS_SECONDS, 1.0)
    return staleness_fraction * MAX_LIVE_STALENESS_PENALTY


def _normalized_score(score: float) -> float:
    return min(max(score, 0.0), 1.0)
