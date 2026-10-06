# Feed health and motion

These are the first perception checks (R5/R6). They accept the `uint8` BGR camera
tiles returned by `extract_tiles`. No model, network connection, or switcher is
needed. Install the existing `perception` extras to get NumPy and OpenCV.

```python
from atem_ai_vision_mixer.perception.health import check_health
from atem_ai_vision_mixer.perception.motion import motion_score

# The caller keeps a separate previous result for each camera.
previous = history.get(camera_id)
result = check_health(tile, previous)
motion = motion_score(
    result.thumbnail,
    previous.thumbnail if previous is not None else None,
)
history[camera_id] = result
```

Initialize `history = {}` at the start of a stream and clear a camera's entry when
resetting that stream. The later aggregator will own this dictionary. The current
scenario CLI is not yet connected to perception.

`HealthResult` contains `feed_healthy`, named `defects`, a read-only 64×36 grayscale
thumbnail, a raw-pixel hash, and a count capped at three identical frames. The
function accepts the raw tile plus the previous result because a thumbnail and
previous hash alone cannot establish a raw-frame repeat count. It retains no full
frame and reads no clock. Keep thumbnails and hashes local; only derived health,
defect, and motion fields belong in `WorldState`.

| Defect | Initial rule | Feed usable? |
|---|---|---|
| `black` | Thumbnail mean ≤5 and standard deviation ≤2 | No |
| `frozen` | Three identical raw frames and near-zero thumbnail difference | No |
| `soft` | Thumbnail Laplacian variance <20 | Yes |
| `blown` | At least half the thumbnail pixels ≥235 | Yes |
| `crushed` | At least half the thumbnail pixels ≤16 | Yes |

Quality thresholds are named constants in `health.py`, pending tuning with room
footage. Several defects may coexist. Black detection identifies near-uniform
black content; it does not recognize arbitrary “no signal” graphics.

The first frame starts the repeat count at one; the third identical frame is
frozen. Any pixel or dimension change resets the count. Hashing the original BGR
pixels prevents a static live feed from being declared frozen when sensor noise
disappears during downscaling. This assumes a failed feed repeats identical decoded
pixels; a device generating changing noise or overlays needs further evidence.

Motion is mean absolute thumbnail difference divided by 255. It is zero for the
first observation and for identical thumbnails, and one for an all-black to
all-white change. Resizing and grayscale conversion happen once per health check;
motion reuses the resulting thumbnail.

## Validation

Behavior tests use synthetic NumPy arrays and include strided multiview crops,
changing raw noise with identical thumbnails, freeze recovery, per-camera history,
quality-only defects, BGR channel order, and normalized motion.

Run the local four-camera timing checkpoint explicitly:

```bash
ATEM_RUN_PERFORMANCE=1 .venv/bin/python -B -m pytest -p no:cacheprovider \
  tests/test_motion.py -m performance -s
```

It measures 100 ticks after warm-up on four 960×540 crops from a 1080p frame,
including crop extraction, resizing, raw hashing, health, and motion. The 95th
percentile must be under 5 ms. It is opt-in to avoid timing failures on shared CI
machines. A passing developer-machine result does not establish the final M1 Pro
budget for the full perception pipeline.
