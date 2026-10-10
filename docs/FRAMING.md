# Framing quality and movement

`check_framing` turns a local `PeopleObservation` into composition features, a
score in `[0, 1]`, named defects, and a two-second movement history. It does not
run inference, read frames, consult a switcher, or suggest a cut. Keep the last
result for each camera and pass it back on that camera's next recorded frame.

```python
from atem_ai_vision_mixer.perception.framing import check_framing

previous = framing_history.get(camera_id)
result = check_framing(
    observation,
    frame_width=tile.shape[1],
    frame_height=tile.shape[0],
    pts=captured_frame.pts,
    previous=previous,
)
framing_history[camera_id] = result
# The later aggregator can copy result.framing_score and result.defects
# into the existing CameraState; do not serialize result.history.
```

## Feature units

The owner confirmed subject-height normalization for headroom. This follows R10
and task group 10, resolving the design table's conflicting `bbox_top / frame_h`
formula. The following formulas are the implemented contract:

| Feature | Meaning |
|---|---|
| `subject_size` | Box height divided by frame height; intentionally changes with shot size. |
| `headroom` | Box top divided by box height, measured in subject heights. Can be negative for a clipped head. |
| `thirds_distance` | Horizontal distance from box center to the nearest vertical third, divided by box height. |
| `edge_cut` | Box touches or crosses any frame edge. |
| `joint_cut` | Bottom edge falls near a reliable knee or elbow within the image width. |
| `wander_ratio` | Net displacement divided by total path length in the recent two seconds. |

Uniformly scaling pixel coordinates and frame dimensions preserves composition
features and score. Headroom and thirds distances remain comparable in subject
heights. Size remains a frame fraction because it describes how tight the shot
is. Pose coordinates are normalized by MediaPipe; the joint tolerance converts
them back to a distance in subject heights.

## Score and defects

The named constants at the top of `perception/framing.py` are initial estimates,
not weights validated against room footage. Headroom and subject size each have
weight 0.35; thirds placement has weight 0.30. Headroom credit peaks at 0.1
subject heights. Size receives full credit between 0.25 and 0.9 frame heights;
very small or oversized boxes receive less. An edge cut subtracts 0.25, and any
knee/elbow cut subtracts another 0.25. The final score is clamped to `[0, 1]`.

Defects are `headroom_tight`, `headroom_excess`, `off_thirds`,
`subject_too_small`, `subject_too_large`, `edge_cut`, `cut_at_knee`, and
`cut_at_elbow`. Each joint defect is named once, even if both sides intersect
the bottom. Visibility and presence use the people detector's confidence
threshold. A joint outside the image width, away from the bottom, or with
nonfinite coordinates does not claim a cut. Joint indices follow Google's
[documented pose landmark order](https://developers.google.com/edge/mediapipe/solutions/vision/pose_landmarker#pose_landmarker_model).

A missing subject produces score zero, `subject_missing`, and empty history.
Other missing-subject features are zero/false placeholders, not evidence of good
composition. A present subject without a finite, positive box raises an error.
Frame dimensions must be positive integers and timestamps must be finite and
strictly increasing when continuing a history. Start with `previous=None` after
seeking backward or switching recordings.

## Movement history and limits

The functions have no hidden state and never modify the previous result. History
stores recorded timestamps and box centers, with both axes divided by frame
height to preserve Euclidean distances. Samples older than two seconds are
dropped. No movement, one sample, or no samples produces ratio zero; a straight
path produces one, and returning to the starting point produces zero.

Subject loss, a recording gap beyond the window, changed frame dimensions, or a
change in pose-landmark availability restarts history. This prevents a switch
between face and body box centers from inventing movement. Keep histories local;
they are omitted from the result's representation but remain available in the
object. Do not send history, boxes, landmarks, or raw frames to an LLM.

Face-only results still use a face box, not body height. Their framing is a
proxy; joint defects cannot be inferred without pose. The detector does not
identify a speaker, so replacing the detected person in a crowded shot can
still resemble movement. A body box is an estimate from reliable pose points,
not a segmentation outline. Framing weights, crowd accuracy, and the full
pipeline budget need later recorded-footage validation. Aggregation is the next
task group; existing WorldState, directors, controllers, and CI are unchanged.

## Local verification

Tests use synthetic boxes and landmarks for composition, cuts, movement, replay,
and camera-state compatibility. Like the other perception tests, they skip when
the optional NumPy dependency is absent; with the perception extra installed,
all 48 cases ran. The complete suite passed 273 tests with one optional timing
test skipped. Framing coverage was 100%, above the spec's 90% checkpoint.

A local run used real MediaPipe 1.0.0 geometry containers populated with synthetic
data and processed four cameras over 1,000 ticks at recorded 0.1-second intervals.
The four-camera framing arithmetic peaked at 0.11 ms and each history held at
most 21 samples. Static, traversing, edge-cut, and knee-cut cases produced the
expected features and valid `CameraState` objects. This timing excludes decoding
and model inference; it is not a full pipeline performance result. No model
downloads, real frames, or hardware were needed for this task.
