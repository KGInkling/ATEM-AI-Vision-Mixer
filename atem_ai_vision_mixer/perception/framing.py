"""Pure framing arithmetic with explicit, caller-owned movement history."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from math import hypot, isfinite

from atem_ai_vision_mixer.perception.people import (
    LANDMARK_CONFIDENCE_MIN,
    PeopleObservation,
)

# Initial composition targets need tuning against recorded room footage.
HEADROOM_TARGET = 0.1  # Space above the subject, measured in subject heights.
HEADROOM_MIN = 0.02  # Below this, the subject is too close to the top edge.
HEADROOM_MAX = 0.2  # Excess space above the subject weakens composition.
THIRDS_DISTANCE_MAX = (
    0.15  # Allowed distance from a vertical third, in subject heights.
)
THIRDS_SCORE_DISTANCE = 0.5  # A half-subject-height offset exhausts placement credit.
SUBJECT_SIZE_MIN = 0.25  # Very small subjects receive less size credit.
SUBJECT_SIZE_MAX = 0.9  # Leave room around the body instead of filling the frame.
JOINT_CUT_BAND = 0.04  # Bottom edge within this fraction of subject height of a joint.
WANDER_WINDOW_SECONDS = 2.0  # Keep recent motion rather than the whole recording.

HEADROOM_WEIGHT = 0.35  # Reward breathing room above the subject.
THIRDS_WEIGHT = 0.3  # Reward placement near a vertical third.
SUBJECT_SIZE_WEIGHT = 0.35  # Reward a readable subject with space around them.
EDGE_CUT_PENALTY = 0.25  # Clipping any side of the box reduces quality.
JOINT_CUT_PENALTY = 0.25  # Cutting at a knee or elbow is worse than between joints.

# MediaPipe's documented 33-landmark order; no legacy solutions API is needed.
JOINT_GROUPS = (("cut_at_knee", (25, 26)), ("cut_at_elbow", (13, 14)))


@dataclass(frozen=True)
class FramingResult:
    """Carry composition features and a local history snapshot for one camera."""

    timestamp: float
    frame_size: tuple[int, int] = field(repr=False)
    has_pose: bool = field(repr=False)
    subject_size: float = 0.0
    headroom: float = 0.0
    thirds_distance: float = 0.0
    edge_cut: bool = False
    joint_cut: bool = False
    wander_ratio: float = 0.0
    framing_score: float = 0.0
    defects: tuple[str, ...] = ()
    history: tuple[tuple[float, float, float], ...] = field(default=(), repr=False)


def check_framing(
    observation: PeopleObservation,
    *,
    frame_width: int,
    frame_height: int,
    pts: float,
    previous: FramingResult | None = None,
) -> FramingResult:
    """Score one detection; pass the previous result from the same camera only."""
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in (frame_width, frame_height)
    ):
        raise ValueError("Frame dimensions must be positive integers")
    if not isfinite(pts) or (previous is not None and pts <= previous.timestamp):
        raise ValueError("Frame timestamp must be finite and strictly increasing")
    frame_size = (frame_width, frame_height)
    has_pose = bool(observation.pose_landmarks)
    if not observation.subject_present:
        return FramingResult(pts, frame_size, has_pose, defects=("subject_missing",))

    box = observation.bounding_box
    if (
        box is None
        or not all(
            isfinite(value)
            for value in (box.origin_x, box.origin_y, box.width, box.height)
        )
        or box.width <= 0
        or box.height <= 0
    ):
        raise ValueError("A present subject requires a finite positive bounding box")

    center_x = box.origin_x + box.width / 2
    center_y = box.origin_y + box.height / 2
    subject_size = box.height / frame_height
    headroom = box.origin_y / box.height
    thirds_distance = (
        min(abs(center_x - frame_width / 3), abs(center_x - 2 * frame_width / 3))
        / box.height
    )
    edge_cut = (
        box.origin_x <= 0
        or box.origin_y <= 0
        or box.origin_x + box.width >= frame_width
        or box.origin_y + box.height >= frame_height
    )
    defects = []
    if headroom < HEADROOM_MIN:
        defects.append("headroom_tight")
    elif headroom > HEADROOM_MAX:
        defects.append("headroom_excess")
    if thirds_distance > THIRDS_DISTANCE_MAX:
        defects.append("off_thirds")
    if subject_size < SUBJECT_SIZE_MIN:
        defects.append("subject_too_small")
    elif subject_size > SUBJECT_SIZE_MAX:
        defects.append("subject_too_large")
    if edge_cut:
        defects.append("edge_cut")
    joint_defects = _joint_defects(observation, box.height, frame_height)
    defects.extend(joint_defects)

    headroom_credit = _clamp(1 - abs(headroom - HEADROOM_TARGET) / HEADROOM_TARGET)
    thirds_credit = _clamp(1 - thirds_distance / THIRDS_SCORE_DISTANCE)
    size_credit = _clamp(
        min(
            subject_size / SUBJECT_SIZE_MIN, (1 - subject_size) / (1 - SUBJECT_SIZE_MAX)
        )
    )
    score = _clamp(
        HEADROOM_WEIGHT * headroom_credit
        + THIRDS_WEIGHT * thirds_credit
        + SUBJECT_SIZE_WEIGHT * size_credit
        - EDGE_CUT_PENALTY * edge_cut
        - JOINT_CUT_PENALTY * bool(joint_defects)
    )

    history = ()
    if (
        previous is not None
        and previous.frame_size == frame_size
        and previous.has_pose == has_pose
    ):
        history = tuple(
            sample
            for sample in previous.history
            if sample[0] >= pts - WANDER_WINDOW_SECONDS
        )
    # Normalize both axes by frame height to preserve Euclidean distances.
    # Mixing face and body box centers would invent motion, so restart above.
    history += ((pts, center_x / frame_height, center_y / frame_height),)
    return FramingResult(
        timestamp=pts,
        frame_size=frame_size,
        has_pose=has_pose,
        subject_size=subject_size,
        headroom=headroom,
        thirds_distance=thirds_distance,
        edge_cut=edge_cut,
        joint_cut=bool(joint_defects),
        wander_ratio=wander_ratio([(x, y) for _, x, y in history]),
        framing_score=score,
        defects=tuple(defects),
        history=history,
    )


def wander_ratio(positions: Sequence[tuple[float, float]]) -> float:
    """Return net displacement divided by path length, or zero without movement."""
    if any(not isfinite(x) or not isfinite(y) for x, y in positions):
        raise ValueError("Wander positions must be finite")
    if len(positions) < 2:
        return 0.0
    path_length = sum(
        hypot(current[0] - before[0], current[1] - before[1])
        for before, current in pairwise(positions)
    )
    if path_length == 0:
        return 0.0
    first, last = positions[0], positions[-1]
    return _clamp(hypot(last[0] - first[0], last[1] - first[1]) / path_length)


def _joint_defects(observation, subject_height, frame_height) -> list[str]:
    defects = []
    for name, indices in JOINT_GROUPS:
        for index in indices:
            if index >= len(observation.pose_landmarks):
                continue
            point = observation.pose_landmarks[index]
            if (
                isfinite(point.x)
                and isfinite(point.y)
                and 0 <= point.x <= 1
                and (
                    point.visibility is None
                    or point.visibility >= LANDMARK_CONFIDENCE_MIN
                )
                and (
                    point.presence is None or point.presence >= LANDMARK_CONFIDENCE_MIN
                )
                and abs((1 - point.y) * frame_height) / subject_height <= JOINT_CUT_BAND
            ):
                defects.append(name)
                break
    return defects


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))
