# Design: Perception

## Approach

Build perception in order of increasing difficulty, so each stage is verifiable before the next
one adds uncertainty: **feed health and motion → audio VAD → people detection → framing →
aggregation**. The first stage needs no models at all and catches the failure that matters most
operationally (a dead camera). The last stage needs the most tuning.

Everything runs from recorded files. The `FrameSource` protocol defined here is the seam that
spec 5's live DeckLink capture slots into — get the interface right and the swap changes one
line in `app.py`.

### Why PyAV rather than `cv2.VideoCapture`

`cv2.VideoCapture` is the obvious choice and the wrong one: **it does not decode audio at all.**
This project's single most valuable signal is the speech pause, and a cut has to land on a
specific pause relative to a specific frame. PyAV decodes both streams from one container with a
shared timebase, so `frame.pts` and the audio chunk's `pts` are directly comparable.

It is also the same library the DeckLink path uses in spec 5, where ffmpeg muxes raw video and
PCM into a NUT stream on one pipe. Using PyAV now means `FileSource` and `DeckLinkSource` are
structurally the same code with a different input.

### Why thumbnails for health and motion

Every health and motion check runs on a 64×36 grayscale thumbnail, not the full tile. The
information these checks need — is it black, is it frozen, is anything moving — survives
aggressive downscaling completely, and the cost drops by three orders of magnitude. This is what
keeps the whole health pass under 5 ms for all cameras (R12) and leaves the frame budget for the
models.

### Why frozen-frame detection needs two signals

Naive frozen detection compares consecutive frames and calls it frozen when the difference is
near zero. That misfires constantly: a locked-off camera pointed at an empty stage produces
almost no difference either, and flagging it unhealthy would veto a perfectly good wide shot.

The distinction is that a live sensor always produces noise, so consecutive frames are never
*byte-identical*, whereas a frozen SDI feed repeats the same bytes. So: near-zero `cv2.absdiff`
**and** an identical hash of the raw tile across ≥3 frames means frozen; near-zero difference
with differing hashes means merely static, which is fine.

### Why silero-vad over WebRTC VAD

The input is the program mix — a lectern mic through a PA, with music beds, congregational
singing, applause, and room reverb. WebRTC VAD is a GMM designed for narrowband telephony; in a
sanctuary it reports "speech" through the entire worship set. Silero is trained across many
languages and noise conditions and holds up.

Critically, load it through **onnxruntime, not PyTorch**. The `silero-vad` package declares
`torch` + `torchaudio`, roughly 2.5 GB, on a machine where memory is the binding constraint. The
ONNX path is officially supported by the project and runs the same model.

### Why pose is not computed for every tile

Pose estimation is the expensive model here. Running it on every camera every frame would blow
the 100 ms budget on a 16 GB M1 Pro that is also decoding 1080p and — later — hosting a local
LLM. It is also unnecessary: framing quality only needs to be precise for the camera that is
live and the one being considered as a challenger. Everything else needs only "is a person
there," which cheap detection answers.

---

## Components / changes

New package `atem_ai_vision_mixer/perception/`, plus `capture/` and one tool.

- `capture/source.py` — the `FrameSource` protocol and a `CapturedFrame` dataclass
  (`video: np.ndarray`, `audio: np.ndarray | None`, `pts: float`).
- `capture/file_source.py` — PyAV implementation. Demuxes video and audio from one container,
  yields `CapturedFrame`. `realtime` flag paces to frame rate when set.
- `capture/tiles.py` — `extract_tiles(frame, tile_config) -> dict[int, np.ndarray]`. Rectangles
  come from config. A `discrete` mode passes frames through unchanged for the per-camera-input
  case.
- `perception/health.py` — `check_health(thumbnail, prev_thumbnail, prev_hash) -> HealthResult`.
  Black via `cv2.meanStdDev`; frozen via `cv2.absdiff` + frame hash; soft via
  `cv2.Laplacian(...).var()`; blown/crushed via histogram tails.
- `perception/motion.py` — `motion_score(thumbnail, prev_thumbnail) -> float`, mean absolute
  difference normalized to `[0, 1]`.
- `perception/audio_vad.py` — `VadState` machine wrapping the silero ONNX model. Holds the LSTM
  hidden state across calls (the model is stateful — this is easy to get wrong). Applies
  hysteresis, tracks pause duration, and grades it.
- `perception/people.py` — MediaPipe **Tasks** wrappers: `FaceDetector` and `PoseLandmarker`.
- `perception/framing.py` — pure functions from detections to framing features and named
  defects, plus the `wander_ratio` tracker.
- `perception/aggregator.py` — `PerceptionPipeline.tick(captured_frame, switcher_state) ->
  WorldState`. Owns the per-camera history needed for motion, freeze, and wander.
- `tools/make_multiview.py` — composes N clips into a synthetic multiview.

### MediaPipe: the API most examples get wrong

⚠️ **`mediapipe.solutions` no longer exists.** It was removed in the 0.10.3x series. Every
tutorial, Stack Overflow answer, and blog post using `mp.solutions.pose` or
`mp.solutions.face_detection` is dead code against current MediaPipe and will fail on import.

Use the Tasks API: `mediapipe.tasks.python.vision.FaceDetector` and
`mediapipe.tasks.python.vision.PoseLandmarker`, with `RunningMode.VIDEO` so the tracker can use
temporal continuity. Model bundles (`.task` files) must be downloaded and their path given in
config — they are not bundled with the wheel.

### Framing math

All normalized by subject height so the numbers mean the same thing at any shot size:

| Feature | Computation | Good range |
|---|---|---|
| `subject_size` | `bbox_h / frame_h` | drives shot-type classification |
| `headroom` | `bbox_top / frame_h` | 0.05–0.12; <0.02 crops the head |
| `thirds` | distance from nearest vertical third | smaller is better |
| `edge_cut` | subject bbox touches frame edge | penalty |
| `joint_cut` | frame bottom falls at knee or elbow band | penalty — the classic ugly frame |
| `wander_ratio` | net displacement ÷ path length over ~2 s | ≈1 traversing, ≪1 gesturing in place |

`framing_score` is a weighted sum with the penalties subtracted, clamped to `[0, 1]`. Weights
live as named module constants with a comment each, not as inline magic numbers.

---

## Do NOT touch

- `execution/` and `director/` from spec 1 — this spec feeds them, it does not change them.
- `world_state.py` — the contract is already fixed. If perception needs a field that isn't
  there, that is a change to spec 1 and should be raised, not worked around.
- Repository CI configuration — that is spec 2.

---

## Correctness properties

1. A `WorldState` produced by the aggregator always passes Pydantic validation.
2. A camera reported `feed_healthy: False` always carries at least one defect explaining why.
3. A static-but-live camera (identical content, differing bytes) is **never** reported frozen.
4. `audio.pause_ms` is `0` whenever `audio.speaking` is `True`, and monotonically increasing
   while it stays `False`.
5. `framing_score` and `motion_score` are always within `[0.0, 1.0]`.
6. Perception is a pure function of its inputs plus its own declared history — running the same
   file twice produces the same `WorldState` sequence.

Property 6 matters as much here as determinism did in spec 1: it is what makes a recorded
service a repeatable test fixture.

---

## Verification strategy

- **Unit tests** per module, using tiny synthetic frames built with numpy rather than real
  video — a 64×36 all-black array tests black detection far more clearly than a video file does.
- **Golden-file test**: run the full pipeline over a short synthetic multiview, write the
  `WorldState` sequence to JSONL, and compare against a committed golden file. This catches
  unintended changes to any feature at once.
- **Frozen-vs-static test** is worth calling out separately, since it is the subtle one: feed
  the pipeline (a) a genuinely duplicated frame sequence and (b) a static scene with synthetic
  noise added, and assert only the first is flagged.
- **Performance test**: assert the health+motion pass over 4 tiles completes in under 5 ms and
  the full tick under 100 ms, on the developer machine. Mark it so CI can skip it on slower
  shared runners rather than flaking.
- **Runtime verification**: run the pipeline over a synthetic multiview and dump `WorldState`
  JSONL. Read it against the video. It should describe what a person sees.

---

## Dependency notes

```bash
pip install av mediapipe onnxruntime silero-vad
```

- `mediapipe` pulls in **`opencv-contrib-python`**. Do **not** also install `opencv-python` —
  the two collide on the `cv2` namespace. Let MediaPipe win; contrib is a superset.
- `onnxruntime` requires **macOS 14+**.
- If WebRTC VAD is ever wanted for comparison, the installable package is **`webrtcvad-wheels`**,
  not `webrtcvad` — the latter has no arm64 wheel and compiles from C source.
- On Python 3.11, `numpy` caps at 2.4.x and `scipy` at 1.17.x. Pin so a future `pip install -U`
  doesn't silently no-op.

---

## Open questions

1. **Tile rectangles are unknown until hardware exists.** Blackmagic publishes multiview
   *layouts* but not per-tile pixel dimensions. The config format is designed so the numbers can
   be filled in after measuring, and `make_multiview.py` lets the code be exercised meanwhile
   with known-correct rectangles. Not blocking.
2. **Framing weights are guesses.** The weighting of headroom vs thirds vs subject size can only
   be tuned against real footage of the actual room and the actual speaker. Expect to revisit
   after the first recorded service. They are grouped as named constants for that reason.
3. **Does program audio arrive on the multiview feed?** Unverified — it determines whether the
   VAD input comes from the multiview capture or a separate aux. The `FrameSource` interface
   makes either work, so this can stay open until bring-up.
