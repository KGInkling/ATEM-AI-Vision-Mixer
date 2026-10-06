"""Normalized thumbnail motion and four-camera runtime proof (R6, R12)."""

import os
from time import perf_counter

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from atem_ai_vision_mixer.capture.tiles import TileRectangle, extract_tiles
from atem_ai_vision_mixer.perception.health import check_health
from atem_ai_vision_mixer.perception.motion import motion_score


@pytest.mark.parametrize(
    "before,after,expected",
    [(0, 0, 0.0), (0, 255, 1.0), (255, 0, 1.0), (64, 128, 64 / 255)],
)
def test_motion_is_normalized_without_unsigned_subtraction_wrap(
    before, after, expected
):
    previous = np.full((36, 64), before, dtype=np.uint8)
    current = np.full((36, 64), after, dtype=np.uint8)

    assert motion_score(current, previous) == pytest.approx(expected)


def test_first_frame_has_no_observed_motion():
    assert motion_score(np.full((36, 64), 255, dtype=np.uint8), None) == 0.0


def test_motion_averages_the_entire_thumbnail():
    previous = np.zeros((36, 64), dtype=np.uint8)
    current = previous.copy()
    current[:, :32] = 255

    assert motion_score(current, previous) == 0.5


@pytest.mark.parametrize(
    "shape,dtype",
    [((72, 128), np.uint8), ((36, 64, 3), np.uint8), ((36, 64), np.float32)],
)
def test_motion_rejects_non_thumbnail_input(shape, dtype):
    invalid = np.zeros(shape, dtype=dtype)
    valid = np.zeros((36, 64), dtype=np.uint8)

    with pytest.raises(ValueError, match="64.*36"):
        motion_score(invalid, None)
    with pytest.raises(ValueError, match="64.*36"):
        motion_score(valid, invalid)


@pytest.mark.performance
@pytest.mark.skipif(
    os.environ.get("ATEM_RUN_PERFORMANCE") != "1",
    reason="Opt in to the local timing checkpoint with ATEM_RUN_PERFORMANCE=1",
)
def test_four_camera_health_and_motion_under_five_ms():
    """Include crop views, resizing, and raw hashes in the per-tick budget."""
    frame = np.random.default_rng(42).integers(32, 224, (1080, 1920, 3), dtype=np.uint8)
    config = {
        i + 1: TileRectangle(x, y, 960, 540)
        for i, (x, y) in enumerate([(0, 0), (960, 0), (0, 540), (960, 540)])
    }
    previous = {}
    elapsed = []
    for index in range(110):
        start = perf_counter()
        for camera, tile in extract_tiles(frame, config).items():
            old = previous.get(camera)
            result = check_health(tile, old)
            motion_score(result.thumbnail, old.thumbnail if old else None)
            previous[camera] = result
        duration = perf_counter() - start
        if index >= 10:
            elapsed.append(duration)

    p95 = float(np.percentile(elapsed, 95))
    print(f"Four-camera health + motion p95: {p95 * 1000:.3f} ms")
    assert p95 < 0.005
