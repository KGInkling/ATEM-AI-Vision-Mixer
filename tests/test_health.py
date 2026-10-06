"""Image-content and history regressions for feed health (R5)."""

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("cv2")

from atem_ai_vision_mixer.perception.health import check_health


def _tile(value=100, height=36, width=64):
    return np.full((height, width, 3), value, dtype=np.uint8)


def test_black_feed_is_unhealthy_with_an_explanation():
    result = check_health(_tile(0))

    assert not result.feed_healthy
    assert "black" in result.defects


def test_dark_scene_with_contrast_is_not_black():
    tile = _tile(0)
    tile[:, :4] = 64

    result = check_health(tile)

    assert result.feed_healthy
    assert "black" not in result.defects


def test_freeze_requires_three_consecutive_identical_frames_and_recovers():
    tile = _tile()
    first = check_health(tile)
    second = check_health(tile.copy(), first)
    third = check_health(tile.copy(), second)
    changed = check_health(_tile(101), third)

    assert first.feed_healthy and second.feed_healthy
    assert not third.feed_healthy
    assert "frozen" in third.defects
    assert changed.feed_healthy
    assert "frozen" not in changed.defects
    assert changed.identical_frames == 1


def test_raw_noise_discarded_by_downscaling_does_not_look_frozen():
    previous = None
    for index in range(6):
        tile = _tile(height=360, width=640)
        tile[0, index, 0] += 1
        result = check_health(tile, previous)
        if previous is not None:
            assert np.array_equal(result.thumbnail, previous.thumbnail)
            assert result.frame_hash != previous.frame_hash
        assert result.feed_healthy
        assert "frozen" not in result.defects
        previous = result


def test_freeze_history_is_separate_for_each_camera():
    frozen = live = None
    for value in [100, 101, 102]:
        frozen = check_health(_tile(100), frozen)
        live = check_health(_tile(value), live)

    assert not frozen.feed_healthy
    assert live.feed_healthy


@pytest.mark.parametrize(
    "value,defect", [(100, "soft"), (255, "blown"), (10, "crushed")]
)
def test_quality_defects_do_not_veto_an_otherwise_usable_feed(value, defect):
    result = check_health(_tile(value))

    assert result.feed_healthy
    assert defect in result.defects


def test_detailed_midtone_scene_has_no_defects():
    tile = _tile(70)
    tile[::2, ::2] = 180
    tile[1::2, 1::2] = 180

    assert check_health(tile).defects == ()


def test_histogram_tails_require_a_substantial_fraction():
    tile = _tile()
    tile[0, 0] = 0
    tile[0, 1] = 255

    result = check_health(tile)

    assert "blown" not in result.defects
    assert "crushed" not in result.defects


def test_thumbnail_uses_bgr_and_does_not_alias_capture_buffers():
    tile = _tile()
    tile[:] = (255, 0, 0)
    result = check_health(tile)
    saved = result.thumbnail.copy()
    tile[:] = 0

    assert result.thumbnail.shape == (36, 64)
    assert result.thumbnail.dtype == np.uint8
    assert np.all(result.thumbnail == 29)
    assert np.array_equal(result.thumbnail, saved)


def test_strided_multiview_crop_and_contiguous_copy_have_same_identity():
    frame = _tile(height=108, width=256)
    tile = frame[:, 64:192]
    assert not tile.flags.c_contiguous

    first = check_health(tile)
    second = check_health(tile.copy(), first)
    third = check_health(tile, second)

    assert first.frame_hash == second.frame_hash
    assert not third.feed_healthy


def test_resolution_change_resets_freeze_history_even_with_same_raw_bytes():
    tile = _tile()
    previous = check_health(tile, check_health(tile))
    resized_shape = tile.reshape(72, 32, 3)

    result = check_health(resized_shape, previous)

    assert result.feed_healthy
    assert result.identical_frames == 1


@pytest.mark.parametrize(
    "tile",
    [
        np.zeros((0, 64, 3), dtype=np.uint8),
        np.zeros((36, 64), dtype=np.uint8),
        np.zeros((36, 64, 4), dtype=np.uint8),
        np.zeros((36, 64, 3), dtype=np.float32),
    ],
)
def test_rejects_images_outside_the_capture_bgr_contract(tile):
    with pytest.raises(ValueError, match="BGR"):
        check_health(tile)


def test_replaying_the_same_sequence_has_identical_results():
    sequence = [_tile(value) for value in [100, 100, 100, 101, 0, 255]]

    def replay():
        previous = None
        results = []
        for tile in sequence:
            previous = check_health(tile, previous)
            results.append(
                (previous.feed_healthy, previous.defects, previous.identical_frames)
            )
        return results

    assert replay() == replay()
