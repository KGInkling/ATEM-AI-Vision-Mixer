# Local Ollama shot suggestions

`director/local_llm.py` implements LLM-directors task group 4. `LocalLLMDirector`
has the same `decide(state) -> ShotIntent` interface as `RulesDirector`, and uses
the existing `AsyncRunner` for one model request at a time. It supplies advice to
the deterministic controller; it has no switcher reference or write capability.
Application wiring and the layered decision cadence belong to later groups.

## Model and request

The baseline is `qwen3:4b`, listed upstream as 4.02B parameters, Q4_K_M and 2.5 GB.
This matches the spec's approximately 4B, 4-bit target. Actual resident memory,
warm/cold latency and shot quality on the production Mac remain unmeasured.
See the [official model record](https://ollama.com/library/qwen3:4b).

Each request uses:

- `format=shot_intent_schema(camera_ids)`, with sorted connected camera IDs.
- `temperature=0`, `num_predict=128`, `stream=False`, and `keep_alive=-1`.
- `think=False`, so the small generation budget is used for the JSON answer.
- Trusted instructions and the schema in the first system message, followed by
  policy enums and the volatile WorldState summary in the final user message.

The system prefix stays byte-identical for an unchanged camera set. A changed
camera set necessarily changes its enum. Policy changes remain in the user
context; arbitrary policy rationale is excluded. These settings follow
[Ollama structured outputs](https://docs.ollama.com/capabilities/structured-outputs)
and [thinking controls](https://docs.ollama.com/capabilities/thinking).

## Privacy boundary

The prompt contains only timestamp, numeric camera IDs and live duration,
speaking/pause/level scalars, per-camera health/presence/scores, fixed known defect
codes, and the policy's segment/shot/tempo enums. It excludes raw frames, PCM,
faces/landmarks/boxes, names, transcripts, unknown defect text, policy rationale,
and extra observation fields. The explicit projection is validated before JSON
serialization by `director/prompt_context.py`, shared with the cloud adapter.
This preserves the local request's fields and fixed defect vocabulary. Invalid
scalar mutations and nonfinite JSON values are rejected
before SDK use without data-bearing serializer warnings.

The client targets `http://127.0.0.1:11434` explicitly. Environment host/proxy
settings are ignored, redirects are not followed, and a request hook removes
Authorization because the SDK otherwise picks up `OLLAMA_API_KEY`. No credential
is needed for this local path. The adapter never pulls a model or starts a server.
Operators remain responsible for the trusted loopback daemon and local model.

## Results and failures

Before a result arrives, each call returns current rules output. A valid model
response is parsed with strict `ShotIntent` validation and checked against the
request's camera enum. If output is malformed, the runner logs only the error
type and retains the previous valid result. Before returning retained advice,
the director checks that its camera is still present; otherwise it uses rules.
An empty camera set skips the model and preserves the existing rules hold behavior.

Missing SDKs, connection failures, HTTP errors and missing models use current
rules output. An unavailable episode is logged once on the control thread;
successful recovery re-arms that notice. Worker results carry availability so
expired calls cannot update the control thread's fallback state. Returned model
intents are detached copies, preventing caller edits from corrupting retention.

The default logical request deadline is 2 seconds, with the same finite SDK I/O
timeout. HTTPX timeouts limit I/O inactivity, rather than total request wall time;
`AsyncRunner` separately discards late output. A progressing or stuck call can
occupy the worker until it physically ends. `close()` stops new model calls
without waiting, and each SDK client is closed in its worker's context manager.
See [runner lifecycle](ASYNC_RUNNER.md) and
[HTTPX timeout semantics](https://www.python-httpx.org/advanced/timeouts/).

## Use and verification

SDK imports occur in the worker, so importing or constructing the director works
with only the core dependencies. Install the declared `llm` extra before an
authorized live rehearsal; this change was verified with Ollama 0.6.3 and HTTPX
0.28.1 in a disposable environment, without changing the project venv.

```python
from atem_ai_vision_mixer.director.local_llm import LocalLLMDirector

director = LocalLLMDirector(timeout_seconds=2.0)
try:
    intent = director.decide(world_state)  # validated caller-provided WorldState
    # The controller receives the intent; it retains every veto.
finally:
    director.close()
```

Replace `director.policy` from the control thread with a validated `ShotPolicy`
when cloud guidance is available. The caller will set the local decision cadence.

The existing integration owner tests request shape, private-field canaries,
strict output/camera validation, retention, changed camera sets, outages/recovery,
missing SDK, frozen request snapshots, real shadow-controller ticks, deadline
expiry, and cleanup. All tests use SDK/transport stubs. An additional offline
real-SDK MockTransport proof verifies serialized JSON, finite timeout extensions,
authentication removal despite environment settings, redirect fallback and client
cleanup. Isolated removal of auth/proxy/redirect controls failed that proof as
expected, without live transport or repository mutations.

```bash
.venv/bin/python -B -m pytest -p no:cacheprovider tests/test_integration.py -q
```

Full validation passed 363 tests with 2 existing opt-in timing skips. Package
coverage was 99.87%; local director, all director modules and controller were
100%. Real model inference, grammar enforcement by a running daemon, memory,
cadence tuning, comparative cut quality and application activation are later
checkpoints. No live-model or deployment success is claimed here.
