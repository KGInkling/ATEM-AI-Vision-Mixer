"""Exercise the offline multiview tool with small, real A/V recordings."""

import importlib
import json
from fractions import Fraction
from pathlib import Path

import pytest

from atem_ai_vision_mixer.capture.file_source import FileSource
from atem_ai_vision_mixer.capture.tiles import TileRectangle, extract_tiles

av = pytest.importorskip("av")
np = pytest.importorskip("numpy")
multiview = importlib.import_module("tools.make_multiview")

COLORS = [
    (20, 30, 180),
    (20, 180, 30),
    (180, 30, 20),
    (180, 180, 30),
    (180, 30, 180),
    (30, 180, 180),
    (120, 120, 120),
]


def write_clip(
    path: Path,
    *,
    color: tuple[int, int, int] = COLORS[0],
    rate: int = 2,
    frames: int = 4,
    level: int = 100,
    audio: bool = True,
    origin: int = 0,
    audio_delay: Fraction = Fraction(0),
    square: bool = False,
    changing: bool = False,
) -> None:
    """Create timestamped source media whose expected pixels and PCM are known."""
    with av.open(str(path), "w", format="nut") as output:
        video = output.add_stream("rawvideo", rate=rate)
        video.width, video.height = (80, 80) if square else (160, 90)
        video.pix_fmt = "bgr24"
        sound = output.add_stream("pcm_s16le", rate=8_000) if audio else None
        if sound:
            sound.layout = "mono"
        for index in range(frames):
            pixels = np.empty((video.height, video.width, 3), dtype=np.uint8)
            pixels[:] = tuple(c + (index * 10 if changing else 0) for c in color)
            frame = av.VideoFrame.from_ndarray(pixels, format="bgr24")
            frame.pts, frame.time_base = origin * rate + index, Fraction(1, rate)
            output.mux(video.encode(frame))
            if sound:
                samples = np.full((1, 8_000 // rate), level, dtype=np.int16)
                chunk = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
                chunk.sample_rate = 8_000
                chunk.pts = (
                    origin * 8_000 + int(audio_delay * 8_000) + index * (8_000 // rate)
                )
                chunk.time_base = Fraction(1, 8_000)
                output.mux(sound.encode(chunk))
        output.mux(video.encode(None))
        if sound:
            output.mux(sound.encode(None))


@pytest.mark.parametrize("layout,count", [("4-up", 4), ("7-up", 7)])
def test_layout_timing_and_selected_audio_round_trip(tmp_path, layout, count):
    """Catch wrong geometry, frame-count-based mixing, and choosing the wrong audio."""
    paths = []
    for index in range(count):
        path = tmp_path / f"camera-{index}.nut"
        rate = 2 if index == 0 else 4
        write_clip(
            path,
            color=COLORS[index],
            rate=rate,
            frames=rate if index == count - 1 else rate * 2,
            level=(index + 1) * 100,
            changing=index == 0,
        )
        paths.append(path)
    output = tmp_path / "multiview.mkv"

    config = multiview.make_multiview(
        paths, output, layout=layout, audio_input=2, fps=4
    )

    expected = [(0, 0, 960, 540), (960, 0, 960, 540), (0, 540, 960, 540)]
    expected += (
        [(960, 540, 960, 540)]
        if count == 4
        else [
            (960, 540, 480, 270),
            (1440, 540, 480, 270),
            (960, 810, 480, 270),
            (1440, 810, 480, 270),
        ]
    )
    assert config == {i + 1: TileRectangle(*rect) for i, rect in enumerate(expected)}
    frames = list(FileSource(output))
    assert [f.pts for f in frames] == [0, 0.25, 0.5, 0.75]
    assert all(f.video.shape == (1080, 1920, 3) for f in frames)
    for number, frame in enumerate(frames):
        tiles = extract_tiles(frame.video, config)
        for camera, tile in tiles.items():
            expected_color = np.array(COLORS[camera - 1])
            if camera == 1:
                expected_color += (number // 2) * 10
            center = tile[tile.shape[0] // 2, tile.shape[1] // 2].astype(int)
            assert np.max(np.abs(center - expected_color)) <= 5
    pcm = np.concatenate([f.audio for f in frames if f.audio is not None], axis=-1)
    assert pcm.shape == (1, 8_000)
    assert np.all(pcm == 200)


def test_audio_offset_and_final_chunk_are_preserved(tmp_path):
    """Catch re-clocking audio to zero or allowing its tail past the shortest video."""
    source, short = tmp_path / "source.nut", tmp_path / "short.nut"
    write_clip(source, origin=2, audio_delay=Fraction(1, 4), level=700)
    write_clip(short, frames=2, origin=10, audio=False)
    output = tmp_path / "timed.mkv"

    multiview.make_multiview([source, short], output, fps=4)

    with av.open(str(output)) as container:
        audio = list(container.decode(audio=0))
    assert float(audio[0].pts * audio[0].time_base) == pytest.approx(0.25)
    assert sum(f.samples for f in audio) == 6_000
    assert all(np.all(f.to_ndarray() == 700) for f in audio)
    assert [f.pts for f in FileSource(output)] == [0, 0.25, 0.5, 0.75]


def test_cli_reports_crop_config_and_letterboxes(tmp_path, capsys):
    """Verify the CLI supplies usable crops, keeps aspect ratio, and blanks spare tiles."""
    source = tmp_path / "square.nut"
    write_clip(source, square=True, frames=1)
    output = tmp_path / "view.mkv"

    assert multiview.main([str(source), "--output", str(output), "--fps", "2"]) == 0

    config = json.loads(capsys.readouterr().out)
    assert config == {"1": {"x": 0, "y": 0, "width": 960, "height": 540}}
    frame = next(iter(FileSource(output))).video
    assert np.max(frame[270, 100]) <= 5
    assert np.max(np.abs(frame[270, 480].astype(int) - COLORS[0])) <= 5
    assert np.max(frame[810, 1440]) <= 5


@pytest.mark.parametrize(
    "options,message",
    [
        ({"layout": "bad"}, "layout"),
        ({"fps": 0}, "fps"),
        ({"audio_input": 0}, "audio input"),
        ({"audio_input": 2}, "audio input"),
    ],
)
def test_invalid_options_do_not_create_output(tmp_path, options, message):
    """Reject invalid configuration before decoding or publishing an output."""
    output = tmp_path / "result.mkv"
    with pytest.raises(ValueError, match=message):
        multiview.make_multiview([tmp_path / "unused.nut"], output, **options)
    assert not output.exists()


@pytest.mark.parametrize("count", [0, 5])
def test_input_count_must_fit_layout(tmp_path, count):
    """Reject empty inputs and excess sources rather than silently dropping cameras."""
    with pytest.raises(ValueError, match="input clips"):
        multiview.make_multiview(
            [tmp_path / "unused.nut"] * count, tmp_path / "out.mkv"
        )


def test_existing_output_is_never_overwritten(tmp_path):
    """Protect an existing recording even when its path is selected as output."""
    output = tmp_path / "recording.mkv"
    output.write_bytes(b"keep this recording")
    with pytest.raises(FileExistsError):
        multiview.make_multiview([output], output)
    assert output.read_bytes() == b"keep this recording"


def test_selected_input_must_have_audio(tmp_path):
    """Never substitute another camera's audio when the selected source is silent."""
    source, silent = tmp_path / "sound.nut", tmp_path / "silent.nut"
    write_clip(source)
    write_clip(silent, audio=False)
    output = tmp_path / "result.mkv"
    with pytest.raises(ValueError, match="audio"):
        multiview.make_multiview([source, silent], output, audio_input=2)
    assert not output.exists()


def test_invalid_input_is_reported_without_partial_media(tmp_path, capsys):
    """Show a useful CLI error and leave no output for a missing local input."""
    output = tmp_path / "result.mkv"
    with pytest.raises(SystemExit) as error:
        multiview.main([str(tmp_path / "missing.nut"), "--output", str(output)])
    assert error.value.code == 2
    assert "input file" in capsys.readouterr().err
    assert not output.exists()


def test_audio_must_overlap_the_composed_span(tmp_path):
    """Reject a valid but late audio track rather than publish silent test footage."""
    source = tmp_path / "late.nut"
    write_clip(source, frames=2, audio_delay=Fraction(2))
    output = tmp_path / "view.mkv"
    with pytest.raises(ValueError, match="overlap"):
        multiview.make_multiview([source], output, fps=2)
    assert not output.exists()
    assert not list(tmp_path.glob(".view.mkv.*"))


def test_audio_before_video_is_trimmed_at_zero(tmp_path):
    """Preserve source synchronization without emitting negative output audio time."""
    source = tmp_path / "early.nut"
    write_clip(source, origin=2, audio_delay=Fraction(-1, 4), level=500)
    output = tmp_path / "view.mkv"
    multiview.make_multiview([source], output, fps=2)
    with av.open(str(output)) as container:
        frames = list(container.decode(audio=0))
    assert frames[0].pts == 0
    assert sum(f.samples for f in frames) == 14_000
    assert all(np.all(f.to_ndarray() == 500) for f in frames)


def test_stereo_rate_and_channels_are_preserved(tmp_path):
    """Catch planar/packed channel confusion and unintended sample-rate changes."""
    source = tmp_path / "stereo.nut"
    with av.open(str(source), "w", format="nut") as container:
        video = container.add_stream("rawvideo", rate=2)
        video.width, video.height, video.pix_fmt = 16, 16, "bgr24"
        audio = container.add_stream("pcm_f32le", rate=22_050)
        audio.layout = "stereo"
        for index in range(2):
            frame = av.VideoFrame.from_ndarray(
                np.zeros((16, 16, 3), dtype=np.uint8), format="bgr24"
            )
            frame.pts, frame.time_base = index, Fraction(1, 2)
            container.mux(video.encode(frame))
            samples = np.empty((2, 11_025), dtype=np.float32)
            samples[0], samples[1] = 0.25, -0.25
            chunk = av.AudioFrame.from_ndarray(samples, format="fltp", layout="stereo")
            chunk.sample_rate = 22_050
            chunk.pts, chunk.time_base = index * 11_025, Fraction(1, 22_050)
            container.mux(audio.encode(chunk))
        container.mux(video.encode(None))
        container.mux(audio.encode(None))
    output = tmp_path / "view.mkv"
    multiview.make_multiview([source], output, fps=4)
    with av.open(str(output)) as container:
        frames = list(container.decode(audio=0))
    assert sum(f.samples for f in frames) == 22_050
    assert all(f.sample_rate == 22_050 and len(f.layout.channels) == 2 for f in frames)
    samples = np.concatenate([f.to_ndarray() for f in frames], axis=-1).reshape(-1, 2)
    assert np.all(samples[:, 0] == 8192)
    assert np.all(samples[:, 1] == -8192)


def test_non_matroska_extension_is_rejected(tmp_path):
    """Do not publish a Matroska container under a misleading extension."""
    output = tmp_path / "wrong.mp4"
    with pytest.raises(ValueError, match=".mkv"):
        multiview.make_multiview([tmp_path / "unused.nut"], output)
    assert not output.exists()


def test_fixture_cli_creates_repeatable_local_sources(tmp_path, capsys):
    """Verify the demo provides moving video and distinct audio without real media."""
    fixture = importlib.import_module("tools.make_multiview_fixture")
    directory = tmp_path / "clips"
    assert fixture.main([str(directory)]) == 0
    paths = [Path(line) for line in capsys.readouterr().out.splitlines()]
    assert len(paths) == 7
    for camera, path in enumerate(paths, start=1):
        frames = list(FileSource(path))
        assert len(frames) == 10
        assert frames[0].video.shape == (90, 160, 3)
        assert not np.array_equal(frames[0].video, frames[-1].video)
        samples = np.concatenate(
            [f.audio for f in frames if f.audio is not None], axis=-1
        )
        assert samples.shape == (1, 8_000)
        assert np.argmax(np.abs(np.fft.rfft(samples[0]))) == camera * 110
    with pytest.raises(SystemExit) as error:
        fixture.main([str(directory)])
    assert error.value.code == 2
    assert sorted(directory.iterdir()) == paths


def test_faster_input_is_sampled_by_time(tmp_path):
    """Catch playing a high-rate input in slow motion instead of dropping frames."""
    source = tmp_path / "fast.nut"
    write_clip(source, rate=4, frames=4, changing=True)
    output = tmp_path / "view.mkv"
    multiview.make_multiview([source], output, fps=2)
    frames = list(FileSource(output))
    assert [f.pts for f in frames] == [0, 0.5]
    for index, frame in enumerate(frames):
        expected = np.array(COLORS[0]) + index * 20
        assert np.max(np.abs(frame.video[270, 480].astype(int) - expected)) <= 5


def test_audio_only_input_is_rejected(tmp_path):
    """Report a missing camera stream before creating any output media."""
    source = tmp_path / "audio.wav"
    with av.open(str(source), "w") as container:
        audio = container.add_stream("pcm_s16le", rate=8_000)
        audio.layout = "mono"
        frame = av.AudioFrame.from_ndarray(
            np.zeros((1, 800), dtype=np.int16), format="s16", layout="mono"
        )
        frame.sample_rate = 8_000
        container.mux(audio.encode(frame))
        container.mux(audio.encode(None))
    output = tmp_path / "view.mkv"
    with pytest.raises(ValueError, match="video stream"):
        multiview.make_multiview([source], output)
    assert not output.exists()


def test_untimed_elementary_video_is_rejected(tmp_path):
    """Prevent silently inventing timestamps for an elementary video bitstream."""
    source = tmp_path / "untimed.h264"
    with av.open(str(source), "w", format="h264") as container:
        stream = container.add_stream("libx264", rate=2)
        stream.width, stream.height, stream.pix_fmt = 16, 16, "yuv420p"
        frame = av.VideoFrame.from_ndarray(
            np.zeros((16, 16, 3), dtype=np.uint8), format="bgr24"
        )
        container.mux(stream.encode(frame))
        container.mux(stream.encode(None))
    output = tmp_path / "view.mkv"
    with pytest.raises(ValueError, match="presentation timestamps"):
        multiview.make_multiview([source], output)
    assert not output.exists()


def test_protocol_like_local_filename_stays_local(tmp_path, monkeypatch):
    """Do not let FFmpeg reinterpret a real local filename as a network URL."""
    monkeypatch.chdir(tmp_path)
    write_clip(tmp_path / "http:camera.nut", frames=1)
    real_open = av.open

    def local_open(path, *args, **kwargs):
        assert not isinstance(path, str) or not path.startswith("http:"), (
            "relative filename would invoke the HTTP protocol"
        )
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(av, "open", local_open)
    output = tmp_path / "view.mkv"
    multiview.make_multiview(["http:camera.nut"], output, fps=2)
    assert len(list(FileSource(output))) == 1
