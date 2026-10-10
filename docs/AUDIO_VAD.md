# Speech detection and pause grading

`perception.audio_vad.VadState` implements perception R7/R8. It runs the bundled
Silero v6.2.3 model locally through ONNX Runtime and returns the existing
`AudioState` contract. It neither transcribes speech nor sends audio anywhere.
Install the existing perception extras; do not install `silero-vad`, PyTorch, or
torchaudio.

```python
from atem_ai_vision_mixer.perception.audio_vad import VadState, grade_pause

vad = VadState(sample_rate=16000)
# pcm_block is 512 mono float32 samples, normalized to [-1, 1].
audio_state = vad.process(pcm_block)
if not audio_state.speaking:
    pause_kind = grade_pause(audio_state.pause_ms)
```

## Input and history

- Supported rates are 16 kHz and 8 kHz. Each call consumes exactly 32 ms: 512 or
  256 samples respectively. Other rates, lengths, channel shapes, dtypes, NaNs,
  infinities, and values outside [-1, 1] are rejected before inference.
- The caller must downmix, normalize, resample when needed, and buffer complete
  blocks. Never infer sample rate from an array's length. The current `FileSource`
  returns codec-native arrays without sample-rate metadata; it cannot be passed
  directly to this API. The later capture-to-aggregator integration must resolve
  that metadata boundary explicitly.
- Use one `VadState` per continuous program-audio stream. Recurrent state and the
  model's preceding-sample context are retained between calls. `reset()` clears
  history after a seek, missing/discontinuous audio, or a new recording, while
  reusing an already loaded model. Create a new object to change sample rate.
- Missing audio is not silence: do not insert zero blocks for a missing capture.
  Partial final blocks are the caller's responsibility; this API does not pad them
  and invent pause duration.
- The model loads lazily from a package resource. Its SHA-256 is verified before
  creating the CPU session. No runtime download or alternate-model fallback exists.
- Before importing ONNX Runtime, the loader sets `ORT_DISABLE_TELEMETRY=1` and
  then calls its telemetry-disable API. This is a process-wide opt-out. If another
  component imports the runtime first, set that environment variable before
  starting Python; this module cannot undo earlier initialization events.

## Speech and pause timing

Initial state is not speaking, with zero observed pause. Three consecutive speech
decisions enter speech, and three consecutive non-speech decisions leave it.
Single-frame glitches reset the candidate run rather than switching state.

While speaking, `pause_ms` is always zero. On leaving speech it includes the three
silent blocks used for confirmation (96 ms), then grows by 32 ms per block. During
initial silence it grows from stream start. Tentative speech during a pause does
not reset its duration until speech is confirmed. Time comes only from processed
samples, never the wall clock or video-frame cadence.

`level` is normalized RMS amplitude, independent of the speech probability. The
probability threshold and transition counts are named constants pending evaluation
against room recordings. A positive model decision is not an accuracy guarantee.

| Pause duration | Grade |
|---|---|
| 0–249 ms | `breath` |
| 250–399 ms | `clause` |
| 400–1200 ms | `sentence` |
| Above 1200 ms | `long` |

`grade_pause` classifies any nonnegative integer duration; call it only when not
speaking. Grades remain derived metadata rather than adding fields to `AudioState`.

For deterministic state-machine tests, `advance(speech, level=...)` accepts one
boolean decision per 32 ms. It uses the same hysteresis path as real inference.
Do not mix manually advanced decisions and inferred blocks in one stream, since
that would skip the model's recurrent history.

## Model and validation

The [model record](../atem_ai_vision_mixer/perception/models/README.md) contains the
release, immutable source URL, checksum, input contract, and retained MIT license.
The model is shipped in both source and wheel distributions. Model bytes and audio
stay local; review tools may inspect source and provenance without receiving them.

Tests cover transition hysteresis, sample-based timing, pause boundaries, invalid
input, model-state/context reuse, reset, stream isolation, checksum verification,
and real offline inference at both rates. Real-inference tests require ONNX Runtime;
the state and adapter tests still run in the core-plus-OpenCV coverage environment.
The scenario CLI and full perception aggregator are not wired to this module yet.
