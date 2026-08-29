"""Tests for configurable multiview tile extraction."""

import pytest

from atem_ai_vision_mixer.capture.tiles import TileRectangle, extract_tiles


class _SliceableFrame:
    def __init__(self, shape: tuple[int, ...]) -> None:
        self.shape = shape
        self.selections: list[tuple[slice, slice]] = []

    def __getitem__(self, selection: tuple[slice, slice]) -> str:
        self.selections.append(selection)
        return f"tile-{len(self.selections)}"


def test_extract_tiles_uses_only_configured_rectangles() -> None:
    """Verify camera crops come from configuration rather than hidden geometry."""
    frame = _SliceableFrame(shape=(6, 8, 3))
    config = {
        1: TileRectangle(x=1, y=2, width=3, height=2),
        7: TileRectangle(x=5, y=0, width=3, height=4),
    }

    tiles = extract_tiles(frame, config)

    assert tiles == {1: "tile-1", 7: "tile-2"}
    assert frame.selections == [
        (slice(2, 4), slice(1, 4)),
        (slice(0, 4), slice(5, 8)),
    ]


def test_extract_tiles_crops_real_numpy_pixels() -> None:
    """Verify configured coordinates select the expected pixels from an image."""
    numpy = pytest.importorskip("numpy")
    frame = numpy.arange(6 * 8 * 3, dtype=numpy.uint8).reshape(6, 8, 3)
    rectangle = TileRectangle(x=2, y=1, width=3, height=2)

    tile = extract_tiles(frame, {3: rectangle})[3]

    assert numpy.array_equal(tile, frame[1:3, 2:5])


def test_discrete_mode_preserves_camera_frames_without_cropping() -> None:
    """Verify per-camera inputs produce the same mapping shape as multiview crops."""
    camera_one = object()
    camera_two = object()
    discrete_frames = {1: camera_one, 2: camera_two}

    tiles = extract_tiles(discrete_frames, discrete=True)

    assert tiles == discrete_frames
    assert tiles is not discrete_frames
    assert tiles[1] is camera_one
    assert tiles[2] is camera_two


def test_tile_rectangle_accepts_valid_pixel_bounds() -> None:
    """Verify zero-origin coordinates and positive dimensions are valid."""
    rectangle = TileRectangle(x=0, y=0, width=1, height=1)

    assert rectangle == TileRectangle(x=0, y=0, width=1, height=1)


@pytest.mark.parametrize(
    "values",
    [
        {"x": -1, "y": 0, "width": 1, "height": 1},
        {"x": 0, "y": -1, "width": 1, "height": 1},
        {"x": 0, "y": 0, "width": 0, "height": 1},
        {"x": 0, "y": 0, "width": 1, "height": 0},
    ],
)
def test_tile_rectangle_rejects_unusable_bounds(values: dict[str, int]) -> None:
    """Verify invalid configuration fails before any frame is processed."""
    with pytest.raises(ValueError):
        TileRectangle(**values)


def test_extract_tiles_rejects_rectangle_outside_frame() -> None:
    """Verify a bad hardware measurement cannot silently return a partial crop."""
    frame = _SliceableFrame(shape=(6, 8, 3))
    rectangle = TileRectangle(x=7, y=5, width=2, height=2)

    with pytest.raises(ValueError, match="camera 4"):
        extract_tiles(frame, {4: rectangle})


def test_extract_tiles_requires_configuration_for_multiview() -> None:
    """Verify multiview geometry can never fall back to hard-coded coordinates."""
    with pytest.raises(ValueError, match="tile configuration"):
        extract_tiles(_SliceableFrame(shape=(6, 8, 3)))


def test_extract_tiles_requires_an_image_for_multiview() -> None:
    """Verify a camera mapping cannot accidentally enter the crop path."""
    with pytest.raises(TypeError, match="one image"):
        extract_tiles({1: object()}, {1: TileRectangle(0, 0, 1, 1)})


def test_discrete_mode_requires_a_camera_mapping() -> None:
    """Verify discrete mode cannot silently reinterpret one multiview image."""
    with pytest.raises(TypeError, match="camera-to-frame mapping"):
        extract_tiles(_SliceableFrame(shape=(6, 8, 3)), discrete=True)


def test_extract_tiles_rejects_frame_without_image_dimensions() -> None:
    """Verify crop bounds are checked only against two-dimensional images."""
    with pytest.raises(ValueError, match="height and width"):
        extract_tiles(
            _SliceableFrame(shape=(10,)),
            {1: TileRectangle(0, 0, 1, 1)},
        )
