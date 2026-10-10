"""Composition, movement, and replay contracts for perception R10."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

pytest.importorskip("numpy")

from atem_ai_vision_mixer.perception.framing import check_framing, wander_ratio
from atem_ai_vision_mixer.perception.people import PeopleObservation
from atem_ai_vision_mixer.world_state import CameraState


@dataclass
class Box:
    origin_x: float
    origin_y: float
    width: float
    height: float


def _person(x=160, y=50, width=80, height=500, landmarks=()):
    return PeopleObservation(True, Box(x, y, width, height), tuple(landmarks))


def _check(person=None, *, pts=0.0, previous=None, width=600, height=1000):
    return check_framing(
        _person() if person is None else person,
        frame_width=width,
        frame_height=height,
        pts=pts,
        previous=previous,
    )


def _pose(index, *, x=0.5, y=1.0, visibility=1.0, presence=1.0):
    landmarks = [
        SimpleNamespace(x=0.5, y=0.5, visibility=1.0, presence=1.0) for _ in range(33)
    ]
    landmarks[index] = SimpleNamespace(
        x=x, y=y, visibility=visibility, presence=presence
    )
    return landmarks


def test_good_composition_reports_features_and_no_defects():
    result = _check()

    assert result.subject_size == pytest.approx(0.5)
    assert result.headroom == pytest.approx(0.1)
    assert result.thirds_distance == pytest.approx(0.0)
    assert not result.edge_cut
    assert not result.joint_cut
    assert result.framing_score == pytest.approx(1.0)
    assert result.defects == ()


def test_headroom_and_thirds_are_normalized_by_subject_height():
    small = _check(_person(x=170, y=25, width=40, height=250))
    large = _check(_person(x=80, y=100, width=160, height=1000))

    assert small.headroom == large.headroom == pytest.approx(0.1)
    assert small.thirds_distance == large.thirds_distance == pytest.approx(0.04)
    assert small.subject_size != large.subject_size


def test_uniform_pixel_scaling_preserves_composition():
    original = _check(_person(x=180, y=40, width=80, height=500))
    scaled = _check(
        _person(x=360, y=80, width=160, height=1000), width=1200, height=2000
    )

    for field in ("subject_size", "headroom", "thirds_distance", "framing_score"):
        assert getattr(original, field) == pytest.approx(getattr(scaled, field))
    assert original.defects == scaled.defects


@pytest.mark.parametrize(
    "person,defect",
    [
        (_person(y=0), "headroom_tight"),
        (_person(y=150), "headroom_excess"),
        (_person(x=260), "off_thirds"),
        (_person(height=100, y=10), "subject_too_small"),
        (_person(height=1100, y=110), "subject_too_large"),
    ],
)
def test_named_composition_defects_reduce_the_score(person, defect):
    result = _check(person)

    assert defect in result.defects
    assert result.framing_score < _check().framing_score


@pytest.mark.parametrize(
    "person",
    [_person(x=0), _person(x=520), _person(y=0), _person(y=500), _person(x=-10)],
)
def test_box_touching_any_frame_edge_is_penalized(person):
    result = _check(person)

    assert result.edge_cut
    assert "edge_cut" in result.defects
    assert result.framing_score < 1.0


@pytest.mark.parametrize(
    "index,defect",
    [
        (13, "cut_at_elbow"),
        (14, "cut_at_elbow"),
        (25, "cut_at_knee"),
        (26, "cut_at_knee"),
    ],
)
def test_bottom_cut_near_a_reliable_joint_reports_its_name(index, defect):
    result = _check(_person(landmarks=_pose(index, y=1.01)))

    assert result.joint_cut
    assert defect in result.defects
    assert result.framing_score < _check().framing_score


@pytest.mark.parametrize(
    "kwargs",
    [
        {"y": 0.8},
        {"visibility": 0.1},
        {"presence": 0.1},
        {"x": -0.1},
        {"x": float("nan")},
        {"y": float("nan")},
    ],
)
def test_unreliable_or_nonintersecting_joint_does_not_claim_a_cut(kwargs):
    result = _check(_person(landmarks=_pose(25, **kwargs)))

    assert not result.joint_cut
    assert "cut_at_knee" not in result.defects


def test_multiple_joint_cuts_are_named_once_and_without_pose_none_are_invented():
    landmarks = _pose(25)
    landmarks[26] = landmarks[25]
    landmarks[13] = landmarks[25]
    landmarks[14] = landmarks[25]
    result = _check(_person(landmarks=landmarks))

    assert result.defects.count("cut_at_knee") == 1
    assert result.defects.count("cut_at_elbow") == 1
    assert not _check().joint_cut


@pytest.mark.parametrize(
    "positions,expected",
    [
        ([], 0.0),
        ([(0.0, 0.0)], 0.0),
        ([(0.0, 0.0), (0.0, 0.0)], 0.0),
        ([(0.0, 0.0), (1.0, 1.0), (2.0, 2.0)], 1.0),
        ([(0.0, 0.0), (1.0, 0.0), (0.0, 0.0)], 0.0),
        ([(0.0, 0.0), (1.0, 0.0), (0.5, 0.0)], 1.0 / 3.0),
    ],
)
def test_wander_distinguishes_traversing_from_pacing(positions, expected):
    assert wander_ratio(positions) == pytest.approx(expected)


def test_movement_history_is_a_pure_snapshot_with_a_two_second_window():
    first = _check(_person(x=100), pts=0.0)
    second = _check(_person(x=200), pts=1.0, previous=first)
    third = _check(_person(x=100), pts=2.0, previous=second)
    fourth = _check(_person(x=200), pts=3.01, previous=third)

    assert first.wander_ratio == 0.0
    assert second.wander_ratio == pytest.approx(1.0)
    assert third.wander_ratio == pytest.approx(0.0)
    assert fourth.wander_ratio == pytest.approx(1.0)
    assert len(first.history) == 1
    assert [sample[0] for sample in fourth.history] == [2.0, 3.01]
    assert "history" not in repr(fourth)
    assert fourth == _check(_person(x=200), pts=3.01, previous=third)


def test_subject_loss_gap_resolution_and_pose_change_restart_motion_history():
    first = _check(pts=0.0)
    missing = _check(PeopleObservation(False, None), pts=0.1, previous=first)
    assert missing.framing_score == 0.0
    assert missing.defects == ("subject_missing",)
    assert missing.history == ()
    for previous, pts, kwargs in [
        (missing, 0.2, {}),
        (first, 3.0, {}),
        (first, 0.1, {"width": 1200, "height": 2000}),
    ]:
        result = _check(pts=pts, previous=previous, **kwargs)
        assert result.wander_ratio == 0.0
        assert len(result.history) == 1
    pose = _check(_person(landmarks=_pose(25, y=0.5)), pts=0.1, previous=first)
    assert pose.wander_ratio == 0.0
    face = _check(pts=0.2, previous=pose)
    assert face.wander_ratio == 0.0


def test_each_camera_keeps_its_own_movement_history():
    moving = stationary = None
    for index, x in enumerate([100, 200, 300]):
        moving = _check(_person(x=x), pts=index * 0.1, previous=moving)
        stationary = _check(pts=index * 0.1, previous=stationary)

    assert moving.wander_ratio == pytest.approx(1.0)
    assert stationary.wander_ratio == 0.0


@pytest.mark.parametrize("pts", [float("nan"), float("inf"), 0.0, -1.0])
def test_invalid_or_nonincreasing_timestamp_is_rejected(pts):
    first = _check(pts=0.0)
    with pytest.raises(ValueError, match="timestamp"):
        _check(pts=pts, previous=first)


@pytest.mark.parametrize(
    "width,height", [(0, 100), (100, -1), (1.5, 100), (float("nan"), 100)]
)
def test_invalid_frame_dimensions_are_rejected(width, height):
    with pytest.raises(ValueError, match="dimensions"):
        _check(width=width, height=height)


@pytest.mark.parametrize(
    "person",
    [
        PeopleObservation(True, None),
        _person(width=0),
        _person(height=-1),
        _person(x=float("nan")),
        _person(y=float("inf")),
    ],
)
def test_present_subject_needs_a_finite_positive_box(person):
    with pytest.raises(ValueError, match="box"):
        _check(person)


def test_wander_rejects_nonfinite_points():
    with pytest.raises(ValueError, match="positions"):
        wander_ratio([(0.0, 0.0), (float("nan"), 1.0)])


def test_scores_remain_valid_for_existing_camera_state_under_extreme_framing():
    for person in [
        _person(x=-100, y=-500, height=2000),
        _person(width=1, height=1),
        _person(),
    ]:
        result = _check(person)
        camera = CameraState(
            feed_healthy=True,
            subject_present=True,
            framing_score=result.framing_score,
            motion_score=0.0,
            defects=list(result.defects),
        )
        assert 0.0 <= camera.framing_score <= 1.0
