"""Tests for recorded media decoding on a shared presentation timebase."""

from fractions import Fraction
from pathlib import Path
from typing import Self
from unittest.mock import Mock

import pytest

import atem_ai_vision_mixer.capture.file_source as file_source_module
from atem_ai_vision_mixer.capture.file_source import FileSource


class _FakeStream:
    def __init__(self, stream_type: str) -> None:
        self.type = stream_type


class _FakeFrame:
    def __init__(
        self,
        payload: object,
        pts: int | None,
        time_base: Fraction | None = Fraction(1, 10),
    ) -> None:
        self.payload = payload
        self.pts = pts
        self.time_base = time_base
        self.ndarray_options: list[dict[str, str]] = []

    def to_ndarray(self, **options: str) -> object:
        self.ndarray_options.append(options)
        return self.payload


class _FakePacket:
    def __init__(self, stream: _FakeStream, frames: list[_FakeFrame]) -> None:
        self.stream = stream
        self.frames = frames

    def decode(self) -> list[_FakeFrame]:
        return self.frames


class _FakeContainer:
    def __init__(
        self,
        video_streams: list[_FakeStream],
        audio_streams: list[_FakeStream],
        packets: list[_FakePacket],
    ) -> None:
        self.streams = type(
            "Streams",
            (),
            {"video": video_streams, "audio": audio_streams},
        )()
        self.packets = packets
        self.selected_streams: tuple[_FakeStream, ...] | None = None
        self.closed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *errors: object) -> None:
        self.closed = True

    def demux(self, *streams: _FakeStream) -> list[_FakePacket]:
        self.selected_streams = streams
        return self.packets


class _FakeAvModule:
    def __init__(self, container: _FakeContainer) -> None:
        self.container = container
        self.opened_path: str | None = None

    def open(self, path: str) -> _FakeContainer:
        self.opened_path = path
        return self.container


class _FakeNumpyModule:
    def __init__(self) -> None:
        self.concatenate_calls: list[tuple[list[object], int]] = []

    def concatenate(self, chunks: list[object], axis: int) -> tuple[object, ...]:
        self.concatenate_calls.append((chunks, axis))
        return ("joined", *chunks)


def _dependency_loader(
    av_module: _FakeAvModule,
    numpy_module: _FakeNumpyModule | None = None,
):
    def load(name: str) -> object:
        if name == "av":
            return av_module
        if name == "numpy" and numpy_module is not None:
            return numpy_module
        raise ModuleNotFoundError(name)

    return load


def test_file_source_aligns_audio_to_video_intervals_by_pts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify decoded PCM follows file timestamps even when packets arrive unevenly."""
    video_stream = _FakeStream("video")
    audio_stream = _FakeStream("audio")
    first_video = _FakeFrame("video-0", pts=0)
    second_video = _FakeFrame("video-1", pts=10)
    first_audio = _FakeFrame("audio-0", pts=0)
    future_audio = _FakeFrame("audio-future", pts=15)
    middle_audio = _FakeFrame("audio-middle", pts=5)
    second_audio = _FakeFrame("audio-1", pts=10)
    container = _FakeContainer(
        [video_stream],
        [audio_stream],
        [
            _FakePacket(video_stream, [first_video]),
            _FakePacket(audio_stream, [first_audio]),
            _FakePacket(audio_stream, [future_audio]),
            _FakePacket(audio_stream, [middle_audio]),
            _FakePacket(video_stream, [second_video]),
            _FakePacket(audio_stream, [second_audio]),
        ],
    )
    av_module = _FakeAvModule(container)
    numpy_module = _FakeNumpyModule()
    monotonic = Mock(side_effect=AssertionError("non-realtime decode read the clock"))
    monkeypatch.setattr(
        file_source_module,
        "import_module",
        _dependency_loader(av_module, numpy_module),
    )
    monkeypatch.setattr(file_source_module.time, "monotonic", monotonic)

    frames = list(FileSource("service.nut"))

    assert [frame.video for frame in frames] == ["video-0", "video-1"]
    assert [frame.audio for frame in frames] == [
        ("joined", "audio-0", "audio-middle"),
        ("joined", "audio-1", "audio-future"),
    ]
    assert [frame.pts for frame in frames] == [0.0, 1.0]
    assert first_video.ndarray_options == [{"format": "bgr24"}]
    assert second_video.ndarray_options == [{"format": "bgr24"}]
    assert container.selected_streams == (video_stream, audio_stream)
    assert container.closed is True
    assert av_module.opened_path == "service.nut"
    assert numpy_module.concatenate_calls == [
        (["audio-0", "audio-middle"], -1),
        (["audio-1", "audio-future"], -1),
    ]


def test_file_source_yields_video_when_recording_has_no_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify an audio-less recording still produces usable video records."""
    video_stream = _FakeStream("video")
    container = _FakeContainer(
        [video_stream],
        [],
        [_FakePacket(video_stream, [_FakeFrame("video", pts=25)])],
    )
    av_module = _FakeAvModule(container)
    monkeypatch.setattr(
        file_source_module,
        "import_module",
        _dependency_loader(av_module),
    )

    frames = list(FileSource(Path("silent.nut")))

    assert len(frames) == 1
    assert frames[0].audio is None
    assert frames[0].pts == 2.5
    assert container.selected_streams == (video_stream,)


def test_file_source_preserves_one_audio_chunk_without_concatenating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify one PCM frame stays unchanged and needs no eager NumPy import."""
    video_stream = _FakeStream("video")
    audio_stream = _FakeStream("audio")
    container = _FakeContainer(
        [video_stream],
        [audio_stream],
        [
            _FakePacket(video_stream, [_FakeFrame("video", pts=0)]),
            _FakePacket(audio_stream, [_FakeFrame("audio", pts=0)]),
        ],
    )
    monkeypatch.setattr(
        file_source_module,
        "import_module",
        _dependency_loader(_FakeAvModule(container)),
    )

    frames = list(FileSource("single-audio-frame.nut"))

    assert len(frames) == 1
    assert frames[0].audio == "audio"


def test_file_source_stops_cleanly_when_file_has_no_decodable_frames(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify reaching end of file before a frame returns an empty iteration."""
    video_stream = _FakeStream("video")
    container = _FakeContainer([video_stream], [], [])
    monkeypatch.setattr(
        file_source_module,
        "import_module",
        _dependency_loader(_FakeAvModule(container)),
    )

    assert list(FileSource("empty.nut")) == []
    assert container.closed is True


def test_file_source_rejects_recording_without_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the source fails clearly when no BGR frame stream can be produced."""
    container = _FakeContainer([], [_FakeStream("audio")], [])
    monkeypatch.setattr(
        file_source_module,
        "import_module",
        _dependency_loader(_FakeAvModule(container)),
    )

    with pytest.raises(ValueError, match="video stream"):
        list(FileSource("audio-only.nut"))

    assert container.closed is True


@pytest.mark.parametrize("pts,time_base", [(None, Fraction(1, 10)), (0, None)])
def test_file_source_rejects_frames_without_file_timestamps(
    pts: int | None,
    time_base: Fraction | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify wall-clock time can never substitute for a missing file timestamp."""
    video_stream = _FakeStream("video")
    container = _FakeContainer(
        [video_stream],
        [],
        [_FakePacket(video_stream, [_FakeFrame("video", pts, time_base)])],
    )
    monkeypatch.setattr(
        file_source_module,
        "import_module",
        _dependency_loader(_FakeAvModule(container)),
    )

    with pytest.raises(ValueError, match="presentation timestamp"):
        list(FileSource("missing-timestamp.nut"))


def test_file_source_reports_how_to_install_pyav(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify a core-only install gets an actionable optional-dependency error."""

    def missing_dependency(name: str) -> object:
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(file_source_module, "import_module", missing_dependency)

    with pytest.raises(ModuleNotFoundError, match="perception extra"):
        list(FileSource("service.nut"))


def test_realtime_file_source_paces_against_recorded_pts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify rehearsal playback waits for the file timeline rather than frame count."""
    video_stream = _FakeStream("video")
    container = _FakeContainer(
        [video_stream],
        [],
        [
            _FakePacket(video_stream, [_FakeFrame("video-0", pts=0)]),
            _FakePacket(video_stream, [_FakeFrame("video-1", pts=5)]),
        ],
    )
    monotonic = Mock(side_effect=[100.0, 100.1, 100.2])
    sleep = Mock()
    monkeypatch.setattr(
        file_source_module,
        "import_module",
        _dependency_loader(_FakeAvModule(container)),
    )
    monkeypatch.setattr(file_source_module.time, "monotonic", monotonic)
    monkeypatch.setattr(file_source_module.time, "sleep", sleep)

    frames = list(FileSource("service.nut", realtime=True))

    assert [frame.pts for frame in frames] == [0.0, 0.5]
    assert sleep.call_count == 1
    assert sleep.call_args.args[0] == pytest.approx(0.3)


def test_file_source_reads_a_real_recorded_file(tmp_path: Path) -> None:
    """Verify the PyAV integration decodes real BGR video and PCM without fixtures."""
    av = pytest.importorskip("av")
    numpy = pytest.importorskip("numpy")
    recording = tmp_path / "recording.nut"
    _write_recording(recording, av, numpy)

    frames = list(FileSource(recording))

    assert [frame.pts for frame in frames] == [0.0, 0.5]
    assert all(frame.video.shape == (4, 4, 3) for frame in frames)
    assert all(frame.audio is not None for frame in frames)
    assert all(frame.audio.shape == (1, 4_000) for frame in frames)
    assert numpy.all(frames[0].video == 0)
    assert numpy.all(frames[1].video == 50)
    assert numpy.all(frames[0].audio == 0)
    assert numpy.all(frames[1].audio == 100)


def _write_recording(path: Path, av: object, numpy: object) -> None:
    with av.open(str(path), mode="w", format="nut") as container:
        video_stream = container.add_stream("rawvideo", rate=2)
        video_stream.width = 4
        video_stream.height = 4
        video_stream.pix_fmt = "bgr24"
        audio_stream = container.add_stream("pcm_s16le", rate=8_000)
        audio_stream.layout = "mono"

        for index in range(2):
            pixels = numpy.full((4, 4, 3), index * 50, dtype=numpy.uint8)
            video_frame = av.VideoFrame.from_ndarray(pixels, format="bgr24")
            video_frame.pts = index
            video_frame.time_base = Fraction(1, 2)
            for packet in video_stream.encode(video_frame):
                container.mux(packet)

            samples = numpy.full((1, 4_000), index * 100, dtype=numpy.int16)
            audio_frame = av.AudioFrame.from_ndarray(
                samples,
                format="s16",
                layout="mono",
            )
            audio_frame.sample_rate = 8_000
            audio_frame.pts = index * 4_000
            audio_frame.time_base = Fraction(1, 8_000)
            for packet in audio_stream.encode(audio_frame):
                container.mux(packet)

        for packet in video_stream.encode():
            container.mux(packet)
        for packet in audio_stream.encode():
            container.mux(packet)
