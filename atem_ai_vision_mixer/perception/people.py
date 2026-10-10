"""Local people detection with independent camera histories and selective pose."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import ExitStack
from dataclasses import dataclass, field
from importlib import import_module
from math import ceil, floor, isfinite
from pathlib import Path
from typing import TYPE_CHECKING, Self

import numpy as np

if TYPE_CHECKING:
    from mediapipe.tasks.python.components.containers import (
        BoundingBox,
        NormalizedLandmark,
    )

# Ignore uncertain pose points when estimating a body box for framing.
LANDMARK_CONFIDENCE_MIN = 0.5


@dataclass(frozen=True)
class PeopleObservation:
    """Carry presence and local framing geometry without retaining raw images."""

    subject_present: bool
    bounding_box: BoundingBox | None = field(repr=False)
    pose_landmarks: tuple[NormalizedLandmark, ...] = field(default=(), repr=False)


class PeopleDetector:
    """Own VIDEO tasks per camera; keep at most two pose tasks alive."""

    def __init__(self, face_model_path: str | Path, pose_model_path: str | Path):
        """Load the SDK with explicit local models; never download at inference."""
        self._face_path = _model_path(face_model_path)
        self._pose_path = _model_path(pose_model_path)
        # Keep core-only installations usable without importing the optional SDK.
        self._mp = import_module("mediapipe")
        self._faces = {}
        self._poses = {}
        self._origin: float | None = None
        self._last_ms: int | None = None
        self._closed = False

    def detect(
        self,
        tiles: Mapping[int, np.ndarray],
        pts: float,
        *,
        live_camera: int,
        challenger_camera: int | None = None,
    ) -> dict[int, PeopleObservation]:
        """Detect every tile, with pose only for live/challenger, on file time."""
        if self._closed:
            raise RuntimeError("PeopleDetector is closed")
        selected = {live_camera}
        if challenger_camera is not None:
            selected.add(challenger_camera)
        if not selected.issubset(tiles):
            raise ValueError("Live and challenger camera must have a tile")
        if not isfinite(pts):
            raise ValueError("Frame timestamp must be finite")
        origin = pts if self._origin is None else self._origin
        timestamp_ms = round((pts - origin) * 1000)
        if self._last_ms is not None and timestamp_ms <= self._last_ms:
            raise ValueError("Frame timestamp must map to increasing milliseconds")
        for tile in tiles.values():
            if (
                not isinstance(tile, np.ndarray)
                or tile.dtype != np.uint8
                or tile.ndim != 3
                or tile.shape[2] != 3
                or min(tile.shape[:2]) == 0
            ):
                raise ValueError("People detection requires nonempty uint8 BGR tiles")

        observations = {}
        try:
            # A tracker must never see frames from another camera or retain an
            # expensive pose model after that camera leaves the selected pair.
            for tasks, keep in ((self._faces, tiles), (self._poses, selected)):
                for camera in list(tasks):
                    if camera not in keep:
                        tasks.pop(camera).close()
            for camera, tile in tiles.items():
                image = self._mp.Image(
                    image_format=self._mp.ImageFormat.SRGB,
                    data=np.ascontiguousarray(tile[:, :, ::-1]),
                )
                if camera not in self._faces:
                    self._faces[camera] = self._create_task(pose=False)
                faces = self._faces[camera].detect_for_video(image, timestamp_ms)
                box = None
                if faces.detections:
                    best = max(
                        faces.detections, key=lambda face: face.categories[0].score
                    )
                    box = best.bounding_box
                landmarks = ()
                if camera in selected:
                    if camera not in self._poses:
                        self._poses[camera] = self._create_task(pose=True)
                    pose = self._poses[camera].detect_for_video(image, timestamp_ms)
                    if pose.pose_landmarks:
                        landmarks = tuple(pose.pose_landmarks[0])
                        body_box = self._body_box(landmarks, tile.shape)
                        if body_box is not None:
                            box = body_box
                observations[camera] = PeopleObservation(
                    box is not None, box, landmarks
                )
        except Exception:
            # Some tasks may already have consumed this timestamp. Discard all
            # histories so a caller can retry without mixing old/new state.
            self.reset()
            raise

        self._origin = origin
        self._last_ms = timestamp_ms
        return observations

    def _create_task(self, *, pose: bool):
        vision = self._mp.tasks.vision
        options = self._mp.tasks.BaseOptions(
            model_asset_path=str(self._pose_path if pose else self._face_path),
            delegate=self._mp.tasks.BaseOptions.Delegate.CPU,
        )
        if pose:
            return vision.PoseLandmarker.create_from_options(
                vision.PoseLandmarkerOptions(
                    base_options=options,
                    running_mode=vision.RunningMode.VIDEO,
                    num_poses=1,
                )
            )
        return vision.FaceDetector.create_from_options(
            vision.FaceDetectorOptions(
                base_options=options, running_mode=vision.RunningMode.VIDEO
            )
        )

    def _body_box(self, landmarks, shape) -> BoundingBox | None:
        reliable = [
            point
            for point in landmarks
            if isfinite(point.x)
            and isfinite(point.y)
            and (
                point.visibility is None or point.visibility >= LANDMARK_CONFIDENCE_MIN
            )
            and (point.presence is None or point.presence >= LANDMARK_CONFIDENCE_MIN)
        ]
        if not reliable:
            return None
        height, width = shape[:2]
        left = floor(min(point.x for point in reliable) * width)
        top = floor(min(point.y for point in reliable) * height)
        right = ceil(max(point.x for point in reliable) * width)
        bottom = ceil(max(point.y for point in reliable) * height)
        if right <= left or bottom <= top:
            return None
        # Preserve off-frame coordinates so framing can identify edge cuts.
        return self._mp.tasks.components.containers.BoundingBox(
            origin_x=left, origin_y=top, width=right - left, height=bottom - top
        )

    def reset(self) -> None:
        """Release every history before seeking or replaying a different file."""
        tasks = [*self._faces.values(), *self._poses.values()]
        self._faces.clear()
        self._poses.clear()
        self._origin = None
        self._last_ms = None
        with ExitStack() as stack:
            for task in tasks:
                stack.callback(task.close)

    def close(self) -> None:
        """Release native resources and prevent further inference."""
        self._closed = True
        self.reset()

    def __enter__(self) -> Self:
        """Keep the detector's native lifetime within a with block."""
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        """Release resources even when processing a frame fails."""
        self.close()


def _model_path(path: str | Path) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise FileNotFoundError(
            f"People detection model must be a local file: {resolved}"
        )
    return resolved
