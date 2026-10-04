# Synthetic multiview footage

This development tool makes one 1920×1080 recording from local video clips. It needs no ATEM,
capture card, model, network connection, or separate ffmpeg executable.

From the repository root, install the existing optional dependencies:

```bash
python -m pip install --editable '.[perception,dev]'
```

Create a 4-up recording, using the second input's audio as the program track:

```bash
.venv/bin/python tools/make_multiview.py \
  /path/camera-1.mp4 /path/camera-2.mp4 /path/camera-3.mp4 /path/camera-4.mp4 \
  --layout 4-up --audio-input 2 --output /tmp/multiview.mkv > /tmp/tiles.json
```

The output is Matroska with H.264 video and PCM16 audio. Audio keeps the selected track's sample
rate, channels, and timing relative to its first video frame. The output directory must support
hard links, as local APFS/ext4 directories do: the completed file is published without replacing
an existing recording. Invalid inputs or a failed conversion do not publish a partial result.

## Layout and timing

Input order determines camera numbers, starting at 1. Supply between one clip and the layout's
capacity; unused slots are black and omitted from the printed crop configuration.

- `4-up`: four 960×540 quadrants, ordered top-left, top-right, bottom-left, bottom-right.
- `7-up`: cameras 1–3 use the first three large quadrants. Cameras 4–7 fill the bottom-right
  quadrant as a 2×2 grid of 480×270 tiles.

This follows the ATEM quadrant layout model, where a quadrant can contain four small windows.
The pixel rectangles are synthetic fixture geometry, not measurements of your hardware.
See the [Blackmagic SDK multiview layout reference](https://documents.blackmagicdesign.com/DeveloperManuals/ATEMSDKManual.pdf?_v=1702368010000).

Frames are centered and scaled to fit each tile without stretching their pixel dimensions.
Different input rates are sampled using their recorded presentation timestamps: frames are held
or dropped to produce `--fps` (30 by default). Each clip starts at its first video frame; output
stops at the shortest video's end, rounded up to the output cadence. A final frame needs a
recorded duration or a reported frame rate. Missing or non-increasing video timestamps fail.

Audio before the selected clip's first video frame is trimmed. A later audio start stays later;
a shorter audio track is not padded. Audio beyond the composed video's end is trimmed to that
end. A selected track with no audio overlapping the output is rejected instead of silently
substituting another camera's track. Inputs must be local regular files.

## Reproducible fixture without real recordings

Generate seven one-second clips with moving markers and distinct tones, then compose either
layout. Use a **new directory outside the checkout**; the fixture command refuses existing ones.

```bash
.venv/bin/python tools/make_multiview_fixture.py /tmp/atem-fixture-clips
.venv/bin/python tools/make_multiview.py /tmp/atem-fixture-clips/camera-{1,2,3,4}.nut \
  --layout 4-up --output /tmp/atem-4up.mkv > /tmp/atem-4up-tiles.json
.venv/bin/python tools/make_multiview.py /tmp/atem-fixture-clips/camera-*.nut \
  --layout 7-up --audio-input 2 --output /tmp/atem-7up.mkv > /tmp/atem-7up-tiles.json
```

The tones are test signals, not speech fixtures for VAD. Generated files are intentionally not
committed. Remove your generated clips, multiview recordings, and crop images when finished.

## Decode and crop with the existing capture interface

```python
import json
from pathlib import Path

from atem_ai_vision_mixer.capture.file_source import FileSource
from atem_ai_vision_mixer.capture.tiles import TileRectangle, extract_tiles

rectangles = {
    int(camera): TileRectangle(**bounds)
    for camera, bounds in json.loads(
        Path("/tmp/atem-7up-tiles.json").read_text()
    ).items()
}
for captured in FileSource("/tmp/atem-7up.mkv"):
    cameras = extract_tiles(captured.video, rectangles)
    # captured.pts is the recorded timestamp; captured.audio contains decoded PCM chunks.
    # Process cameras and audio locally. This does not yet run the perception pipeline.
```

## Validation

`tests/test_make_multiview.py` uses small generated recordings to verify geometry, resampling by
video timestamps, selected audio and its offsets, PCM channel/rate preservation, error handling,
and compatibility with `FileSource` and `extract_tiles`. It needs PyAV/NumPy; the existing macOS
integration job installs those dependencies. Core-only environments skip these media tests.

The package coverage gate does not measure standalone `tools/` scripts. Their tests and local
coverage can be run separately without changing that gate:

```bash
.venv/bin/python -B -m pytest -p no:cacheprovider tests/test_make_multiview.py
.venv/bin/python -B -m coverage run --data-file=/tmp/atem-tools.coverage \
  --source=tools -m pytest -p no:cacheprovider tests/test_make_multiview.py
.venv/bin/python -m coverage report --data-file=/tmp/atem-tools.coverage --fail-under=85
```

The last command reports tool coverage; it does not replace or weaken the repository's CI gates.
