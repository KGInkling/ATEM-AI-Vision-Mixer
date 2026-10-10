# Perception golden sequence

`perception.jsonl` contains 20 expected `WorldState` snapshots at 10 Hz. It is
authored from the fixture's declared geometry and sample timeline, not refreshed
from pipeline output. No video, audio, box, or landmark data is committed here.

The fixture in `test_aggregator.py` has four 960×540 tiles in a 1080p frame:

- Camera 1 repeats a flat image, then becomes black at 0.3 s.
- Camera 2 has small raw-byte noise that disappears during thumbnail scaling.
  Its declared pose box has good headroom and sits on a vertical third.
- Camera 3 repeats identical pixels and becomes frozen on its third observation.
- Camera 4 has the same live-noise pattern as camera 2, with a declared pose box
  crossing the left and top edges. Its named framing defects are expected.

The PCM has amplitude 0.25 from 0 through 0.5 s, then zero. A deterministic
speech-prediction double marks a 32 ms block positive if its last 512 samples
contain amplitude above 0.125. Three positive decisions enter speaking. The
block ending at 0.512 s still contains nonzero samples; the following three
silent blocks confirm pause at 0.608 s. The 0.7 s snapshot has 160 ms of observed
pause, and the 1.9 s snapshot has 1,376 ms. The first snapshot consumes no future
audio and has zero observed pause.

People/model predictions are controlled so expected JSON is independent of
MediaPipe or ONNX model revisions. Health, motion, framing, hysteresis, audio
buffering, and WorldState validation run through their real production owners.
One test supplies native capture arrays; another encodes and decodes a temporary
NUT recording with real PyAV and compares the same baseline. Both replay twice.

Run:

```bash
.venv/bin/python -B -m pytest -p no:cacheprovider tests/test_aggregator.py -k golden
```

A mismatch requires investigating the changed behavior and its requirement.
Do not update the file just to make a failed test pass. The JSON checks exact
numeric values; a deliberate semantic change needs review of the affected
expected fields. Generated recordings and actual JSONL dumps are deleted by the
recorded-file test even when it fails.
