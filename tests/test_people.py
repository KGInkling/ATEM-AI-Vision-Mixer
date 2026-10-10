"""People detection and selective per-camera tracking contracts (R9)."""

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

np = pytest.importorskip("numpy")

from atem_ai_vision_mixer.perception.people import PeopleDetector


@dataclass
class Box:
    origin_x: int
    origin_y: int
    width: int
    height: int


def _landmark(x, y, visibility=1.0, presence=1.0):
    return SimpleNamespace(x=x, y=y, visibility=visibility, presence=presence)


class FakeTask:
    def __init__(self, factory, options):
        self.factory = factory
        self.options = options
        self.calls = []
        self.closed = False

    def detect_for_video(self, image, timestamp_ms):
        assert not self.closed
        if self.calls:
            assert timestamp_ms > self.calls[-1][1]
        self.calls.append((image.data.copy(), timestamp_ms))
        if self.factory.fail:
            raise RuntimeError("inference failed")
        return self.factory.result

    def close(self):
        self.closed = True


class FakeFactory:
    def __init__(self, result):
        self.result = result
        self.instances = []
        self.fail = False

    def create_from_options(self, options):
        task = FakeTask(self, options)
        self.instances.append(task)
        return task


@pytest.fixture
def backend(monkeypatch, tmp_path):
    face = FakeFactory(SimpleNamespace(detections=[]))
    pose = FakeFactory(SimpleNamespace(pose_landmarks=[]))
    sdk = SimpleNamespace(
        Image=lambda **kwargs: SimpleNamespace(**kwargs),
        ImageFormat=SimpleNamespace(SRGB="SRGB"),
        tasks=SimpleNamespace(
            BaseOptions=lambda **kwargs: SimpleNamespace(**kwargs),
            vision=SimpleNamespace(
                FaceDetector=face,
                PoseLandmarker=pose,
                FaceDetectorOptions=lambda **kwargs: SimpleNamespace(**kwargs),
                PoseLandmarkerOptions=lambda **kwargs: SimpleNamespace(**kwargs),
                RunningMode=SimpleNamespace(VIDEO="VIDEO"),
            ),
            components=SimpleNamespace(containers=SimpleNamespace(BoundingBox=Box)),
        ),
    )
    sdk.tasks.BaseOptions.Delegate = SimpleNamespace(CPU="CPU")
    monkeypatch.setattr(
        "atem_ai_vision_mixer.perception.people.import_module", lambda name: sdk
    )
    face_path = tmp_path / "face.tflite"
    pose_path = tmp_path / "pose.task"
    face_path.write_bytes(b"fake local face model")
    pose_path.write_bytes(b"fake local pose model")
    return SimpleNamespace(
        face=face, pose=pose, face_path=face_path, pose_path=pose_path
    )


def _detector(backend):
    return PeopleDetector(backend.face_path, backend.pose_path)


def _tiles(*cameras):
    return {camera: np.full((20, 40, 3), camera, dtype=np.uint8) for camera in cameras}


def test_face_runs_on_all_tiles_with_independent_video_histories(backend):
    with _detector(backend) as detector:
        first = detector.detect(_tiles(1, 2, 3, 4), 10.0, live_camera=1)
        detector.detect(_tiles(1, 2, 3, 4), 10.1, live_camera=1)

    assert list(first) == [1, 2, 3, 4]
    assert all(not observation.subject_present for observation in first.values())
    assert all(observation.bounding_box is None for observation in first.values())
    assert len(backend.face.instances) == 4
    for camera, task in enumerate(backend.face.instances, start=1):
        assert [timestamp for _, timestamp in task.calls] == [0, 100]
        assert all(np.all(image == camera) for image, _ in task.calls)
        assert task.options.running_mode == "VIDEO"
        assert task.options.base_options.model_asset_path == str(backend.face_path)
        assert task.options.base_options.delegate == "CPU"
        assert task.closed


def test_pose_runs_only_on_live_and_challenger_and_releases_old_selection(backend):
    with _detector(backend) as detector:
        detector.detect(_tiles(1, 2, 3, 4), 0.0, live_camera=1, challenger_camera=2)
        live_pose, old_challenger = backend.pose.instances
        detector.detect(_tiles(1, 2, 3, 4), 0.1, live_camera=1, challenger_camera=3)
        assert np.all(live_pose.calls[-1][0] == 1)
        assert np.all(old_challenger.calls[-1][0] == 2)
        assert np.all(backend.pose.instances[-1].calls[-1][0] == 3)
        assert old_challenger.closed
        assert not live_pose.closed
        assert len(live_pose.calls) == 2
        assert sum(not task.closed for task in backend.pose.instances) == 2
        detector.detect(_tiles(1, 2, 3, 4), 0.2, live_camera=3, challenger_camera=3)
        assert sum(not task.closed for task in backend.pose.instances) == 1

    assert all(task.closed for task in backend.pose.instances)
    assert all(task.options.running_mode == "VIDEO" for task in backend.pose.instances)
    assert all(task.options.num_poses == 1 for task in backend.pose.instances)
    assert all(
        task.options.base_options.model_asset_path == str(backend.pose_path)
        for task in backend.pose.instances
    )


def test_converts_strided_bgr_crops_to_rgb_without_changing_capture(backend):
    frame = np.full((20, 80, 3), (10, 20, 30), dtype=np.uint8)
    tile = frame[:, 20:60]
    assert not tile.flags.c_contiguous
    with _detector(backend) as detector:
        detector.detect({1: tile}, 0.0, live_camera=1)

    rgb, _ = backend.face.instances[0].calls[0]
    assert rgb.flags.c_contiguous
    assert np.all(rgb == (30, 20, 10))
    assert np.all(frame == (10, 20, 30))
    assert np.array_equal(backend.pose.instances[0].calls[0][0], rgb)


def test_highest_confidence_face_provides_box_without_face_keypoints(backend):
    boxes = [Box(1, 2, 3, 4), Box(5, 6, 7, 8)]
    backend.face.result.detections = [
        SimpleNamespace(bounding_box=box, categories=[SimpleNamespace(score=score)])
        for box, score in zip(boxes, [0.6, 0.9], strict=True)
    ]
    with _detector(backend) as detector:
        observation = detector.detect(_tiles(1), 0.0, live_camera=1)[1]

    assert observation.subject_present
    assert observation.bounding_box == boxes[1]
    assert observation.pose_landmarks == ()
    assert "bounding_box" not in repr(observation)


def test_pose_detects_person_without_face_and_preserves_off_frame_geometry(backend):
    landmarks = [_landmark(-0.1, 0.1), _landmark(0.8, 1.1)]
    backend.pose.result.pose_landmarks = [landmarks]
    with _detector(backend) as detector:
        observation = detector.detect(_tiles(1, 2), 0.0, live_camera=1)[1]

    assert observation.subject_present
    assert observation.bounding_box == Box(-4, 2, 36, 20)
    assert observation.pose_landmarks == tuple(landmarks)
    assert "pose_landmarks" not in repr(observation)


def test_pose_box_ignores_unreliable_points_and_falls_back_to_face(backend):
    face_box = Box(1, 2, 3, 4)
    backend.face.result.detections = [
        SimpleNamespace(bounding_box=face_box, categories=[SimpleNamespace(score=0.9)])
    ]
    backend.pose.result.pose_landmarks = [
        [
            _landmark(0.2, 0.2),
            _landmark(0.7, 0.8),
            _landmark(-10, -10, visibility=0.1),
            _landmark(10, 10, presence=0.1),
            _landmark(float("nan"), 0.0),
        ]
    ]
    with _detector(backend) as detector:
        observation = detector.detect(_tiles(1), 0.0, live_camera=1)[1]
        assert observation.bounding_box == Box(8, 4, 20, 12)
        backend.pose.result.pose_landmarks = [[_landmark(0.2, 0.2)]]
        observation = detector.detect(_tiles(1), 0.1, live_camera=1)[1]
        assert observation.bounding_box == face_box


def test_pose_without_reliable_extent_does_not_invent_a_subject_box(backend):
    backend.pose.result.pose_landmarks = [[_landmark(0.2, 0.3, visibility=0.1)]]
    with _detector(backend) as detector:
        observation = detector.detect(_tiles(1), 0.0, live_camera=1)[1]

    assert not observation.subject_present
    assert observation.bounding_box is None


def test_invalid_input_does_not_advance_trackers(backend):
    with _detector(backend) as detector:
        detector.detect(_tiles(1, 2), -2.0, live_camera=1)
        for invalid_pts in [-2.0, -3.0, -1.9999, float("nan"), float("inf")]:
            with pytest.raises(ValueError, match="timestamp"):
                detector.detect(_tiles(1, 2), invalid_pts, live_camera=1)
        for bad in [
            np.zeros((0, 40, 3), dtype=np.uint8),
            np.zeros((20, 40), dtype=np.uint8),
            np.zeros((20, 40, 4), dtype=np.uint8),
            np.zeros((20, 40, 3), dtype=np.float32),
        ]:
            with pytest.raises(ValueError, match="BGR"):
                detector.detect({1: _tiles(1)[1], 2: bad}, -1.9, live_camera=1)
        with pytest.raises(ValueError, match="camera"):
            detector.detect(_tiles(1, 2), -1.9, live_camera=3)
        with pytest.raises(ValueError, match="camera"):
            detector.detect(_tiles(1, 2), -1.9, live_camera=1, challenger_camera=3)
        detector.detect(_tiles(1, 2), -1.9, live_camera=1)

    assert len(backend.face.instances) == 2
    assert all(len(task.calls) == 2 for task in backend.face.instances)


def test_camera_removal_and_reset_release_history_for_replay(backend):
    with _detector(backend) as detector:
        detector.detect(_tiles(1, 2), 1.0, live_camera=1)
        detector.detect(_tiles(1), 1.1, live_camera=1)
        assert backend.face.instances[1].closed
        detector.reset()
        assert all(task.closed for task in backend.face.instances)
        assert all(task.closed for task in backend.pose.instances)
        detector.detect(_tiles(1), 0.0, live_camera=1)
        assert backend.face.instances[-1].calls[0][1] == 0


def test_failed_inference_closes_partial_histories_and_allows_retry(backend):
    with _detector(backend) as detector:
        detector.detect(_tiles(1, 2), 1.0, live_camera=1)
        backend.pose.fail = True
        with pytest.raises(RuntimeError, match="inference failed"):
            detector.detect(_tiles(1, 2), 1.1, live_camera=1)
        assert all(task.closed for task in backend.face.instances)
        assert all(task.closed for task in backend.pose.instances)
        backend.pose.fail = False
        detector.detect(_tiles(1, 2), 1.1, live_camera=1)


def test_model_initialization_failure_releases_already_created_tasks(
    backend, monkeypatch
):
    def fail(options):
        raise RuntimeError("invalid model")

    monkeypatch.setattr(backend.pose, "create_from_options", fail)
    with _detector(backend) as detector:
        with pytest.raises(RuntimeError, match="invalid model"):
            detector.detect(_tiles(1), 0.0, live_camera=1)
        assert all(task.closed for task in backend.face.instances)


def test_close_is_idempotent_and_prevents_reuse(backend):
    detector = _detector(backend)
    detector.detect(_tiles(1), 0.0, live_camera=1)
    detector.close()
    detector.close()
    with pytest.raises(RuntimeError, match="closed"):
        detector.detect(_tiles(1), 0.1, live_camera=1)


def test_model_paths_must_be_existing_local_files(backend, tmp_path):
    for face, pose in [
        (tmp_path / "missing.tflite", backend.pose_path),
        (backend.face_path, tmp_path / "missing.task"),
        (tmp_path, backend.pose_path),
    ]:
        with pytest.raises(FileNotFoundError, match="model"):
            PeopleDetector(face, pose)
    assert backend.face.instances == []
    assert backend.pose.instances == []
