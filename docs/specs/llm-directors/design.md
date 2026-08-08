# Design: LLM Director Tiers

## Approach

Three directors, layered by cost and cadence, all subordinate to the same deterministic
controller:

| Tier | Cadence | Emits | Runs where | Fails to |
|---|---|---|---|---|
| **rules** (spec 1) | every tick | `ShotIntent` | in-process | — always available |
| **local** | every few seconds | `ShotIntent` | `ollama serve`, out-of-process | rules |
| **cloud** | every 20–30 s | `ShotPolicy` | AWS Bedrock | last policy, then default |

The division of labour is the important part. **Bedrock sets policy, the local model picks the
shot, the controller decides when.** Bedrock is the only tier smart enough to notice "this is the
sermon body, not the worship set — favour tight shots and cut only on strong pauses," and that
judgement stays valid for tens of seconds. Asking it which camera to use *right now* would be
paying frontier-model latency and cost for a decision that a 4B model makes adequately, 10×
cheaper, with a fraction of the delay.

Calling Bedrock every tick would be roughly 720 calls per service. On the policy cadence it is
closer to 120, for strictly better decisions — the slow tier is doing the thing it is actually
good at.

### Why the LLM is advisory and not authoritative

Nothing here changes `execution/controller.py`, and that is deliberate. The controller's vetoes —
minimum dwell, cut cooldown, feed health, speech-pause gating, rate cap, the preview review
window — apply identically whether the suggestion came from a lookup table or a frontier model.
That property is the entire safety argument for putting an LLM near a live production switcher,
and it survives only if this spec adds no write path.

### Why the local model runs out-of-process

`ollama serve` is a separate process. That gets three things: model memory lives outside the
Python heap on a 16 GB machine that is also decoding 1080p; a model load or crash cannot stall
the frame loop; and the model can be restarted or swapped without restarting the app.

The alternative — loading a model in-process — would put multi-gigabyte allocations in the same
address space as the capture buffers, on the machine least able to afford it.

### Why output is schema-constrained rather than parsed

Both tiers pass a JSON Schema to the runtime so output is grammar-constrained at generation
time. This is categorically different from prompting "reply in JSON" and calling `json.loads`:
the model is *structurally unable* to emit a malformed response or an unknown field.

The `camera` enum is patched in per request from the cameras actually present in the current
`WorldState`, using the `shot_intent_schema()` helper from spec 1. That is what makes it
impossible for a model to name camera 5 when only 1–3 are connected — a class of bug that
prompt engineering cannot reliably eliminate.

Validation can still fail for other reasons (a truncated response, a server error). The response
to that is always the same: log it, keep the previous decision. Never fall back to a random or
partially-parsed one.

---

## Components / changes

New package `atem_ai_vision_mixer/director/` additions:

- `policy.py` — `ShotPolicy` Pydantic model and `Segment` / `ShotType` / `CutTempo` enums, plus
  `DEFAULT_POLICY` used before the first cloud response arrives.
- `local_llm.py` — `LocalLLMDirector`. Talks to `ollama serve` over HTTP, passing
  `format=<ShotIntent schema>`, `temperature=0`, a `num_predict` cap, and `keep_alive=-1`.
- `cloud_llm.py` — `CloudPolicyDirector`. Calls Bedrock, returns a `ShotPolicy`.
- `async_runner.py` — the skip-if-busy wrapper both tiers use.
- `layered.py` — `LayeredDirector` composing the three tiers behind the single `Director`
  protocol, so `app.py` sees one object.

### Local tier configuration

```python
ollama.chat(
    model="qwen3:4b",
    messages=[{"role": "system", "content": SYSTEM},   # stable → cached prefix
              {"role": "user", "content": world_state_json}],  # volatile → last
    format=shot_intent_schema(camera_ids),   # grammar-constrained
    options={"temperature": 0, "num_predict": 128},
    keep_alive=-1,                            # never unload between calls
)
```

`keep_alive=-1` matters more than it looks: without it the model unloads after a few minutes
idle, and the next call pays a multi-second reload right when a decision is needed. `num_predict`
caps a rambling generation so one bad response cannot stall the tier.

Model choice targets a 4B-class model quantized to 4-bit — roughly 3 GB resident. On a 16 GB M1
Pro also running macOS, capture buffers, OpenCV, and a CoreML detector, an 8B model would put the
machine uncomfortably close to swap, and swap is fatal to frame timing.

### Cloud tier configuration

```python
from anthropic import AnthropicBedrockMantle

client = AnthropicBedrockMantle(aws_region="us-east-1")   # region is REQUIRED, no default

client.messages.create(
    model="anthropic.claude-opus-5",          # Bedrock IDs carry the provider prefix
    max_tokens=1024,
    system=[{"type": "text", "text": SYSTEM_AND_SCHEMA,
             "cache_control": {"type": "ephemeral"}}],     # explicit — see below
    output_config={"format": {"type": "json_schema",
                              "schema": ShotPolicy.model_json_schema()},
                   "effort": "low"},
    messages=[{"role": "user", "content": world_state_summary}],
)
```

Four Bedrock-specific details that are easy to get wrong:

1. **The model ID carries an `anthropic.` prefix on Bedrock.** The bare first-party ID returns an
   error.
2. **`aws_region` is required** — unlike some clients there is no default.
3. **Bedrock does not support automatic top-level `cache_control`.** The explicit breakpoint on
   the last system block is not optional here; without it nothing caches.
4. **Batches, the Files API, and task budgets are unavailable on Bedrock.** None are needed, but
   don't reach for them.

`effort: "low"` suits a director: this is a short structured judgement on a small input, not a
reasoning marathon, and latency matters more than depth.

### Prompt structure and caching

Order is `system + schema + few-shot` (stable, byte-identical every call) then `WorldState`
(volatile). Caching is a **prefix match** — a single changed byte anywhere in the prefix
invalidates everything after it.

The failure mode to avoid is putting anything per-request into the stable block: a timestamp, a
service ID, a `datetime.now()` in a header. It looks harmless and silently reduces every call to
a full-price cache miss. Verify by checking that `cache_read_input_tokens` is non-zero on the
second and subsequent calls; if it is always zero, something in the prefix is varying.

### The skip-if-busy pattern

```
tick arrives
  └─ call already in flight?  ── yes ──► skip, return previous decision
                              └─ no  ──► fire async, return previous decision
                                          (result lands for a later tick)
```

Every tick returns immediately with the most recent completed decision. Nothing waits. This is
why a slow or hung model degrades responsiveness to "slightly stale advice" rather than to a
frozen switcher.

---

## Do NOT touch

- **`execution/controller.py`** — the single most important boundary in this spec. LLM output is
  subject to exactly the same vetoes as rule-based output. If a change here seems to require
  loosening a controller rule, that is a design error, not a missing feature.
- `director/rules.py` — it stays as the always-available fallback and the yardstick the LLM tiers
  must beat.
- `world_state.py` / `intent.py` contracts from spec 1.
- `perception/` from spec 3.

---

## Correctness properties

1. No director ever calls a switcher method. (Enforceable by test: pass a switcher whose methods
   raise, run every director, assert nothing raises.)
2. A tick never blocks on a model call — measured tick latency is unaffected by a deliberately
   slow stubbed model.
3. With the local server unreachable and no AWS credentials, the application still produces
   valid decisions every tick, via the rules director.
4. Every returned `ShotIntent` names a camera present in the `WorldState` it was derived from.
5. A validation failure never changes the decision — the previous one is retained unmodified.
6. The number of cloud calls in a run is bounded by the configured cadence.

---

## Verification strategy

- **Unit tests with stubbed transports.** No test requires a running Ollama or AWS credentials
  (R12). Stub the HTTP layer and the Bedrock client; assert request shape (schema passed, enum
  patched, cache breakpoint present, prefix stable across calls) and response handling.
- **A deliberately slow stub** proves property 2: assert tick latency is flat while the stub
  sleeps.
- **A deliberately malformed stub** proves property 5.
- **Prefix-stability test**: build two requests from different `WorldState`s and assert the
  system block is byte-identical. This is the cheapest possible guard against the silent
  cache-invalidation bug.
- **Offline test**: run the whole app with both tiers disabled and assert normal operation.
- **Manual, with real services**: replay a recorded `WorldState` sequence through all three
  directors and diff their cut lists. If the local tier does not beat `rules`, that is worth
  knowing before it goes anywhere near a service.
- **Runtime verification**: a real run against Ollama and Bedrock, capturing the decision log
  with tier attribution and the token-usage report.

---

## Open questions

1. **Which local model.** A 4B-class model is the size constraint; the specific one should be
   chosen by measuring JSON-schema adherence and latency on this exact task, not by benchmark
   reputation. Models below ~4B are documented to be unreliable at structured output even with
   grammar constraints — they produce well-formed JSON containing nonsense. Worth testing 2–3.
2. **Cloud cadence.** 20–30 s is an estimate. The right value is however long a policy stays true
   for, which is a property of the service, not the model. Tune against a recorded service.
3. **Does the cloud tier earn its place?** Genuinely open. If policy guidance does not measurably
   improve shot selection over rules + local, the honest outcome is to drop the tier rather than
   keep it for the architecture diagram. The tier-attribution logging in R11 exists to make that
   answerable.
4. **AWS region and cost ceiling.** Region affects latency; no ceiling is currently specified.
   Consider a per-service call budget that disables the tier once exceeded.
