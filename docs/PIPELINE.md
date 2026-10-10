# Recorded perception pipeline

`PerceptionPipeline.tick(captured_frame, program_state)` returns the existing
validated `WorldState`. It combines configured tile extraction, feed health,
motion, people detection, framing, and local speech detection. It owns histories
for one recording and never reads or writes a switcher.

```python
from atem_ai_vision_mixer.capture.file_source import FileSource
from atem_ai_vision_mixer.capture.tiles import TileRectangle
from atem_ai_vision_mixer.perception.aggregator import PerceptionPipeline
from atem_ai_vision_mixer.world_state import ProgramState

# Example synthetic 1080p 4-up geometry; use measured/configured rectangles.
tiles = {
    1: TileRectangle(0, 0, 960, 540),
    2: TileRectangle(960, 0, 960, 540),
    3: TileRectangle(0, 540, 960, 540),
    4: TileRectangle(960, 540, 960, 540),
}
with PerceptionPipeline(
    tiles,
    face_model_path=face_model_path,
    pose_model_path=pose_model_path,
) as pipeline:
    first_pts = None
    for captured in FileSource(recording_path):
        if first_pts is None:
            first_pts = captured.pts
        # This offline example holds camera 2 live for the whole recording.
        # A real caller supplies its observed program camera and live duration.
        program = ProgramState(live_camera=2, seconds_live=captured.pts - first_pts)
        state = pipeline.tick(captured, program, challenger_camera=4)
        print(state.model_dump_json())
```

Install the existing perception extra and obtain the external MediaPipe assets
as described in [people detection](PEOPLE.md). The Silero model is already bundled.
The scenario CLI remains a scripted simulation; this class is the recorded-file
integration seam. No capture hardware or network is needed during replay.

## Inputs and histories

The caller supplies `ProgramState`; the pipeline copies its live camera and
duration rather than reading a switcher or deriving operator state from video.
Live and challenger IDs must exist in the tile mapping. Pose runs only on those
selected cameras. With `discrete=True`, pass `tile_config=None` and a
`CapturedFrame.video` mapping of camera IDs to BGR arrays.

Camera histories are independent and removed when a camera disappears. Changed
tile dimensions restart health/motion comparisons; framing also retains its
own size and box-source continuity checks. Call `reset()` before seeking backward
or replaying another recording. An inference failure discards partially advanced
histories and propagates the error; reset/replay to restore a continuous stream.
Use a context manager or `close()` to release native models and buffers.

Snapshots contain only core program/audio fields, camera scores, presence, and
named defects. Detailed framing features, wander history, thumbnails, boxes, and
landmarks stay local. They are not added to WorldState or sent to an LLM. No
changes to core contracts, directors, controllers, or CI are required.

## Audio metadata and causality

`CapturedFrame.audio` remains the codec-native PCM array. Its optional
`AudioMetadata` records the sample rate, sample-format name, layout name,
presentation timestamp, and number of samples per channel. `FileSource` populates
these from decoded PyAV frames; old capture-record construction remains valid.
Audio passed to the pipeline must have metadata: encoding and time are never
guessed from array shape. Missing samples must have no metadata.

When joining decoded chunks, FileSource preserves native values and combines
only one encoding on a continuous sample timeline. Gaps, overlaps, or format
changes within a combined video interval raise instead of silently erasing their
timing. Native layout names can be `1 channels`, rather than `mono`; preserve the
decoded label and let PyAV interpret it.

The pipeline reconstructs native audio and uses PyAV to downmix and resample to
16 kHz mono float32. A FIFO retains partial blocks and lookahead audio. Only
complete 512-sample blocks whose end is at or before the video PTS reach Silero.
Thus the first video snapshot can have zero observed pause even if its capture
record includes later silence. A 96 ms block is not consumed at video time
95.99 ms. Pause duration grows from processed samples, not video cadence.

Queued audio can cover later video ticks even when those capture records contain
no new audio. Once the sample timeline no longer covers video time, speech
history resets to `speaking=False`, `pause_ms=0`, `level=0`: no observed cut point,
not a claim of measured silence. New audio after a gap or encoding change starts
a new history. No missing samples or final partial blocks are padded with zeros.
Finite resampling overshoot is clipped to full scale; nonfinite input or output
is rejected. Late audio after the final video timestamp does not affect a snapshot.

## Local verification and limits

The real-model check processed 20 frames of a generated 1080p, four-camera NUT
recording with 44.1 kHz stereo PCM. It correctly identified a black tile and a
frozen tile while small raw-byte changes kept static live tiles healthy. All
snapshots passed WorldState validation, sample-based silence reached 1,888 ms at
video time 1.9 s, and reset/replay produced identical snapshots.

On the M1 Pro, warm ticks peaked at 39.96 ms in this empty-scene recording;
cold model initialization reached 397.79 ms. These measurements exclude file
decoding. Startup and newly selected pose-model loading can exceed the 100 ms
budget. They do not establish a service-wide timing or accuracy guarantee.

A separate ten-frame 1080p multiview of Google's public pose test image detected
people on both selected pose cameras, returned bounded framing scores, and kept
all noisy static feeds healthy. Warm ticks peaked at 42.05 ms; its first tick
took 339.62 ms. The image and pose geometry remained local and were removed after
validation. This is a controlled positive case, not room-footage accuracy proof.

Unit tests use synthetic inputs and test doubles so core CI needs no external
models. The macOS runtime checks use real PyAV, MediaPipe, and local ONNX Runtime.
Temporary recordings, images, downloads, and caches are removed after proof.
Room footage, crowded-scene identity, and complete live-source performance remain
unverified. Golden-file and final cross-requirement tests are the next task group.
