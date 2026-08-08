# Requirements: Perception

Spec 3 of 5. Turns video and audio into a real `WorldState`, replacing the hand-written
scenarios from spec 1. Runs entirely against **recorded video files** — no capture card, no
ATEM. The live capture source is spec 5 and implements the same interface defined here.

**Depends on:** spec 1 (`offline-switching-core`) for the `WorldState` contract.

---

## Requirement 1: Frame source interface

The system SHALL define a `FrameSource` protocol that yields video frames and audio samples on a
**shared timebase**, so an audio decision can be aligned to a specific video frame.

WHEN a `FrameSource` is iterated
THEN it SHALL yield objects carrying a BGR video frame as a numpy array, a PCM audio chunk, and
a presentation timestamp in seconds.

The protocol SHALL be implementable by both a file reader and a live capture device without
either leaking its origin into consumers.

## Requirement 2: File source

The system SHALL provide `FileSource`, reading video and audio from a recorded file.

WHEN a file containing both video and audio is opened
THEN frames and audio chunks SHALL be yielded with timestamps derived from the file's own
timebase, not from wall-clock time.

WHEN the file ends
THEN iteration SHALL stop cleanly rather than raising.

`FileSource` SHALL support a `realtime` flag: when `False` it decodes as fast as possible for
tests; when `True` it paces to the file's frame rate for rehearsal.

## Requirement 3: Synthetic multiview generation

Because the real room is unreachable, the system SHALL provide a tool that composes ordinary
video clips into a synthetic multiview frame for testing.

WHEN `tools/make_multiview.py` is run with N input clips and a layout
THEN it SHALL produce a single video file with the clips tiled in that layout, at 1080p, with
one clip's audio track carried through as the program audio.

Supported layouts SHALL include at minimum 4-up and 7-up, matching the ATEM's real options.

## Requirement 4: Tile extraction

The system SHALL crop a multiview frame into per-camera sub-frames using configured rectangles.

WHEN a multiview frame and a tile configuration are supplied
THEN the system SHALL return a mapping of camera number to sub-frame.

Tile rectangles SHALL come from configuration, never be hard-coded, because the real geometry
can only be measured against actual hardware.

WHEN the source is discrete per-camera feeds rather than a multiview
THEN the same mapping SHALL be produced without tile cropping, so downstream code is unaffected.

## Requirement 5: Feed health

The system SHALL detect unhealthy camera feeds from image content alone.

WHEN a tile is black or shows no signal
THEN `feed_healthy` SHALL be `False` and a `black` defect SHALL be reported.

WHEN a tile is **frozen**
THEN `feed_healthy` SHALL be `False` and a `frozen` defect SHALL be reported.

A frozen feed SHALL be distinguished from a static-but-live camera. A live camera pointed at a
motionless stage still has sensor noise; a genuinely frozen feed repeats byte-identical frames.
Detection SHALL therefore require **both** near-zero frame difference **and** an identical frame
hash across consecutive frames.

WHEN a tile is severely defocused, blown out, or crushed
THEN the corresponding defect SHALL be reported, but `feed_healthy` SHALL remain `True` — these
degrade shot quality rather than making the feed unusable.

Health checks SHALL run on downscaled thumbnails, not full-resolution frames.

## Requirement 6: Motion

The system SHALL produce a `motion_score` in `[0.0, 1.0]` per camera from frame differencing.

WHEN consecutive frames are identical
THEN `motion_score` SHALL be `0.0`.

## Requirement 7: Voice activity detection

The system SHALL detect speech and silence in the program audio.

WHEN speech is present
THEN `audio.speaking` SHALL be `True` and `audio.pause_ms` SHALL be `0`.

WHEN speech has stopped
THEN `audio.pause_ms` SHALL report milliseconds elapsed since speech ended, growing each tick.

Detection SHALL use hysteresis so it does not chatter: entering `SPEAKING` SHALL require several
consecutive speech frames, and entering `PAUSE` SHALL require several consecutive non-speech
frames.

The VAD SHALL run without PyTorch.

## Requirement 8: Pause grading

The system SHALL classify pause length, because not every pause is a cut point.

WHEN a pause is under 250 ms
THEN it SHALL be graded `breath`.

WHEN a pause is 250–400 ms
THEN it SHALL be graded `clause`.

WHEN a pause is 400 ms–1.2 s
THEN it SHALL be graded `sentence` — the grade the controller treats as a cut opportunity.

WHEN a pause exceeds 1.2 s
THEN it SHALL be graded `long`.

## Requirement 9: People detection

The system SHALL detect whether a person is present in each camera tile and where they are.

WHEN a person is detected
THEN `subject_present` SHALL be `True` and a bounding box SHALL be available to the framing
stage.

Pose landmarks SHALL be computed only for the camera currently live and any camera under active
consideration, not for every tile every frame — pose is the expensive model and this is a
16 GB machine.

## Requirement 10: Framing features

The system SHALL compute framing quality from detections, normalized by subject size so the
result is invariant to how tight the shot is.

`framing_score` SHALL combine at minimum: headroom, rule-of-thirds placement, subject size, and
penalties for cutting the subject at the frame edge or at a joint.

The system SHALL also emit **named defects** as strings, not only a composite float. A defect
list like `["headroom_tight", "cut_at_knee"]` is directly usable by a director; a bare `0.42` is
not.

The system SHALL compute `wander_ratio` — net displacement divided by path length over a short
window.

WHEN `wander_ratio` approaches 1
THEN the subject is traversing, which favours a wider shot.

WHEN `wander_ratio` is far below 1
THEN the subject is moving in place, which favours holding a tighter shot.

## Requirement 11: Aggregation into WorldState

The system SHALL assemble all per-camera and audio features into a valid `WorldState`.

WHEN the aggregator is ticked
THEN it SHALL return a `WorldState` that passes Pydantic validation and carries the timestamp of
the frame it was built from.

The aggregator SHALL populate `program.live_camera` and `program.seconds_live` from switcher
state supplied by the caller, not by reading the switcher itself.

## Requirement 12: Performance budget

Perception SHALL sustain at least 10 Hz on an M1 Pro with 16 GB while processing a 1080p
multiview.

WHEN the per-tick perception pass is timed
THEN it SHALL complete within 100 ms at 10 Hz for a 4-camera configuration.

Health and motion checks SHALL complete in under 5 ms for all tiles combined.

## Requirement 13: No regressions (SHALL NOT)

This spec SHALL NOT modify `execution/` or `director/` from spec 1. It produces the `WorldState`
those modules already consume.

This spec SHALL NOT import `pyatem`, `PyATEMMax`, `anthropic`, or `ollama`.

This spec SHALL NOT require a capture card, an ATEM, or a network connection. All tests SHALL
run from recorded files.

This spec SHALL NOT install both `opencv-python` and `opencv-contrib-python` — they collide on
the `cv2` namespace.

This spec SHALL NOT leave temporary or generated media files in the working tree.
