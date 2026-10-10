# Local people detection

`PeopleDetector` accepts the camera-to-BGR-tile mapping from `extract_tiles` and
the recorded frame's presentation timestamp in seconds. It uses MediaPipe Tasks
in `VIDEO` mode, with a separate face tracker for each camera. Pose runs only for
the caller's live camera and optional challenger. Each has its own pose tracker;
at most two pose models remain loaded, and deselected cameras release theirs.

## Install and obtain models

Install the existing perception extra:

```bash
python -m pip install -e '.[perception]'
```

Download models once, outside the repository. Pass both local paths from your
configuration to the constructor. The detector never downloads a model at runtime.
The face asset is a `.tflite` file; the pose asset is a `.task` bundle. Neither is
included in the project wheel.

Google's [face model guide](https://developers.google.com/edge/mediapipe/solutions/vision/face_detector#models)
and [pose model guide](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker#models)
describe the available variants. These version-1 assets were used for local
validation with MediaPipe 1.0.0:

| Model | Download | SHA-256 |
|---|---|---|
| BlazeFace short range | [face model](https://storage.googleapis.com/mediapipe-models/face_detector/blaze_face_short_range/float16/1/blaze_face_short_range.tflite) | `b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f` |
| Pose Landmarker Lite | [pose bundle](https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task) | `59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a` |

Use a compatible full-range face variant when evaluating wider stage shots.
Short-range detection is designed for nearby faces and is not evidence of
accuracy on the room's wide camera. Verify model checksums after downloading.

## Call the detector

```python
from atem_ai_vision_mixer.perception.people import PeopleDetector

# tiles, pts, live_camera, and challenger_camera come from the caller.
with PeopleDetector(face_model_path, pose_model_path) as detector:
    observations = detector.detect(
        tiles,
        pts,
        live_camera=live_camera,
        challenger_camera=challenger_camera,
    )
    present = observations[live_camera].subject_present
```

Every tile must be a nonempty `uint8` BGR array with three channels. Strided
multiview crops are accepted and converted to contiguous RGB for the SDK. The
live and challenger IDs must exist in the supplied mapping. Supplying the same
ID for both runs pose once. Calls are synchronous; use one detector per pipeline
and do not call it concurrently.

Timestamps come from the recording, not the wall clock. The first timestamp is
normalized to zero; negative file timestamps are supported. Later timestamps
must map to strictly increasing milliseconds. Invalid frames or timestamps are
rejected before any tracker advances. Call `reset()` before seeking backward or
replaying another recording. Inference failure releases all partially advanced
histories and propagates the error; the caller can retry with fresh trackers.
Use the context manager or `close()` to release native resources.

## Output and limits

Each `PeopleObservation` contains presence, a MediaPipe pixel `BoundingBox`, and
normalized pose landmarks when pose found a body. A usable pose box takes
precedence over the highest-confidence face box. Pose points with visibility or
presence below 0.5, or nonfinite coordinates, do not contribute to that box.
Off-frame coordinates are retained so framing can identify an edge cut.

On other cameras, presence is a face-detection proxy and the box covers the face,
not the whole body. It can miss a person facing away. On selected cameras, pose
can detect a person even without a visible face. Pose runs for one body; this
does not identify a speaker or guarantee that face and body belong to the same
person in a crowded shot. Framing and aggregation are later task groups.

Raw images, face keypoints, world-space landmarks, and segmentation masks are
not retained in observations. Keep all boxes and landmarks local. Do not log or
send them, or source frames, to a language model. The detector imports no
switcher, networking, or LLM client and cannot write to hardware.

## Validation boundary

Synthetic-array tests use a fake SDK to verify routing, independent VIDEO
histories, color conversion, geometry, and failure cleanup without models or
network access. Local checks with the real SDK and the versioned models above
processed four black tiles without false positives and detected pose on the two
selected cameras using Google's public pose test image. Only aggregate counts
and timings were recorded; the temporary image and model downloads were removed.

On the M1 Pro, six ticks of that positive sample had a steady-processing maximum
of 36.5 ms after initialization. A separate black-tile run peaked at 265 ms while
loading a newly selected camera's pose model. Selection changes deliberately
release and reload models to bound memory. These small checks do not establish
the full pipeline's 100 ms budget or accuracy on room footage.

MediaPipe 1.0.0 initialized macOS graphics services even with the CPU delegate.
The sandboxed native process aborted during that initialization; the same proof
passed when run on the host outside the sandbox. This is a host runtime
requirement, not a reason to disable controller safety checks.
