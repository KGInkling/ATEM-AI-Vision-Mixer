# Tasks: Perception

Work top to bottom. Do not start a group until the checkpoint before it passes.

> **Delivery rhythm:** one pull request per implementation task group, with that group's tests in
> the same PR — **not** one large PR at the end. Checkpoint-only groups are gates you run before
> opening a PR, not PRs themselves. **Hard stop when this spec is complete**: report and wait.
> Full rule in [`../HANDOFF.md`](../HANDOFF.md).

**Read `requirements.md` and `design.md` first.** Requirement numbers (R1–R13) are referenced
throughout.

**Prerequisite:** spec 1 (`offline-switching-core`) must be implemented — this spec produces the
`WorldState` it defines.

**Audience note:** keep it beginner-readable. Plain, explicit Python; named constants over inline
magic numbers; a docstring on every public function saying what it does and why.

The build order below is deliberately easiest → hardest. Resist reordering it: the early stages
need no models and give you a working pipeline to hang the harder stages off.

---

## Task Group 1: Dependencies and capture interface (R1)

- Add a `perception` optional-dependency group to `pyproject.toml`: `av`, `mediapipe`, and
  `onnxruntime`.
- Do **not** install the `silero-vad` Python package. It declares PyTorch and torchaudio even
  when only ONNX inference is wanted (R7).
- ⚠️ Do **not** add `opencv-python`. MediaPipe installs `opencv-contrib-python`, which is a
  superset; installing both breaks the `cv2` namespace (R13).
- `capture/source.py`: `CapturedFrame` dataclass (`video`, `audio`, `pts`) and the `FrameSource`
  protocol. Docstring it as the seam that live capture will later implement.

## Task Group 2: File source and tiles (R2, R4)

- `capture/file_source.py` using **PyAV**, not `cv2.VideoCapture` — VideoCapture cannot decode
  audio at all, and audio is this project's most valuable signal.
- Demux video and audio from one container; yield `CapturedFrame` with `pts` from the file's own
  timebase, never wall-clock.
- Support `realtime: bool` — decode as fast as possible when `False` (tests), pace to frame rate
  when `True` (rehearsal).
- End of file stops iteration cleanly; no exception.
- `capture/tiles.py`: `extract_tiles(frame, tile_config)`. Rectangles from config only — the real
  geometry can't be known until hardware is measured. Include a `discrete` mode that passes
  per-camera frames through unchanged.

## Task Group 3: Synthetic test footage (R3)

- `tools/make_multiview.py`: compose N clips into one 1080p multiview video, 4-up and 7-up
  layouts, carrying one clip's audio through as program audio.
- Generate a short fixture clip and commit it **only if small**; otherwise commit the generator
  and a script to produce it. Do not commit large media (R13).

## Task Group 4: Checkpoint

- `pytest` green. `ruff check .` clean.
- Manually: run `make_multiview.py`, open the output, confirm tiles land where expected, then
  run `extract_tiles` on a frame and write the crops to disk to eyeball them. **Delete the
  crops afterwards** — nothing scratch stays in the tree.

## Task Group 5: Feed health and motion (R5, R6)

The cheapest and most operationally valuable stage. No models.

- Downscale each tile to a 64×36 grayscale thumbnail first. Every check runs on the thumbnail.
- `perception/health.py`:
  - **black**: `cv2.meanStdDev` low on both mean and stddev.
  - **frozen**: near-zero `cv2.absdiff` **AND** an identical raw-tile hash for ≥3 consecutive
    frames. ⚠️ Both conditions are required — a live camera on a motionless stage also has
    near-zero difference, but its sensor noise means the bytes still differ. Flagging that
    unhealthy would veto a perfectly good wide shot (R5).
  - **soft**: `cv2.Laplacian(gray, cv2.CV_64F).var()` below threshold.
  - **blown / crushed**: histogram tail fractions.
  - Only `black` and `frozen` set `feed_healthy: False`. The rest are quality defects.
- `perception/motion.py`: mean absolute difference, normalized to `[0, 1]`.

## Task Group 6: Checkpoint

- Unit tests using **numpy-constructed frames**, not video files — a 64×36 all-black array tests
  black detection far more clearly than a clip does.
- The frozen-vs-static test specifically: assert a duplicated-frame sequence is flagged and a
  static scene with synthetic noise is not.
- Performance: health + motion across 4 tiles under 5 ms (R12).

## Task Group 7: Audio VAD and pause grading (R7, R8)

- Vendor the official Silero ONNX model under `atem_ai_vision_mixer/perception/models/`. Pin it
  to an upstream release, record its source URL and SHA-256 checksum, retain its license notice,
  and ensure the build includes it as a package resource (R7, R13).
- `perception/audio_vad.py` shall load that local model directly with
  `onnxruntime.InferenceSession`, using NumPy arrays for audio and recurrent state. Do not import
  the `silero-vad` Python package, PyTorch, or torchaudio, and do not download a model at runtime
  (R7).
- ⚠️ The model is **stateful** — it carries LSTM hidden state between calls. Hold that state on
  the `VadState` object and pass it back each call. Recreating it per chunk silently degrades
  detection quality, with no error.
- Hysteresis: several consecutive speech frames to enter `SPEAKING`, several consecutive
  non-speech frames to enter `PAUSE`. Without this it chatters.
- Track `pause_ms` since speech ended, and grade it: `<250` breath, `250–400` clause, `400–1200`
  **sentence** (the grade the controller acts on), `>1200` long.

## Task Group 8: Checkpoint

- Test the state machine with a synthetic boolean sequence rather than real audio — feed it a
  list of per-frame speech/no-speech decisions and assert the transitions and `pause_ms`.
- Assert property 4: `pause_ms == 0` whenever `speaking`, monotonically increasing otherwise.
- Assert the VAD loads its model from the installed package without network access and that
  `silero_vad`, `torch`, and `torchaudio` are not importable dependencies.

## Task Group 9: People detection (R9)

- `perception/people.py` using the MediaPipe **Tasks** API:
  `mediapipe.tasks.python.vision.FaceDetector` and `PoseLandmarker`, with `RunningMode.VIDEO`.
- ⚠️ **`mediapipe.solutions` no longer exists.** Every example online using `mp.solutions.pose`
  is dead code and will fail on import. Do not follow those tutorials.
- Model bundles (`.task` files) are not in the wheel — download them and take the path from
  config. Document where to get them.
- Run detection on all tiles; run **pose only on the live camera and the current challenger**
  (R9). Pose is the expensive model and this is a 16 GB machine.

## Task Group 10: Framing (R10)

- `perception/framing.py`, pure functions from detections to features. Normalize everything by
  subject height so numbers mean the same thing at any shot size.
- Compute `subject_size`, `headroom`, thirds distance, `edge_cut`, `joint_cut`, and
  `wander_ratio` (net displacement ÷ path length over ~2 s).
- Emit **both** a composite `framing_score` and a list of **named defect strings**. The names are
  what make the output usable by a director later — `["headroom_tight", "cut_at_knee"]` is
  actionable in a way `0.42` is not (R10).
- Weights as named module constants with a comment each. They are guesses until real footage
  exists; make them easy to find and change.

## Task Group 11: Aggregation (R11)

- `perception/aggregator.py`: `PerceptionPipeline.tick(captured_frame, switcher_state) ->
  WorldState`.
- Owns per-camera history for motion, freeze detection, and wander.
- Takes `program.live_camera` and `seconds_live` from the caller — perception must not read the
  switcher itself.
- Must return a `WorldState` that passes Pydantic validation.

## Task Group 12: Checkpoint

- `pytest` green, `ruff check .` clean.
- Confirm no import of `pyatem`, `PyATEMMax`, `anthropic`, or `ollama` (R13):
  `grep -rE "pyatem|PyATEMMax|anthropic|ollama" atem_ai_vision_mixer/perception atem_ai_vision_mixer/capture`
  returns nothing.

## Task Group 13: Tests

- Every requirement R1–R13 maps to at least one named test.
- **Golden-file test**: run the pipeline over the synthetic multiview, write `WorldState` JSONL,
  compare against a committed golden file. This catches unintended changes to any feature at
  once and is the highest-value test in this spec.
- Assert all six correctness properties from `design.md`.
- Mark the performance test so CI can skip it on shared runners rather than flaking on them.

## Task Group 14: Coverage checkpoint

- Global 85%; `perception/health.py` and `perception/framing.py` at 90% — they are pure
  functions with many branches, so there is no excuse for gaps.

## Task Group 15: Runtime verification

- Run the full pipeline over the synthetic multiview and dump `WorldState` JSONL.
- Read it against the video. It should describe what you see: the right camera flagged unhealthy
  when you black one out, speech and pause tracking the audio, framing defects naming real
  problems.
- Attach a short excerpt as PR evidence.

## Task Group 16: Submit for review

- Branch first; `main` is protected.
- Commit, open a draft PR using the template, include the JSONL excerpt.
- **Clean up**: no generated media, no dumped crops, no scratch files. `git status` shows only
  intended deliverables (R13).
