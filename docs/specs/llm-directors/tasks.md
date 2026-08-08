# Tasks: LLM Director Tiers

Work top to bottom. Do not start a group until the checkpoint before it passes.

> **Delivery rhythm:** one pull request per implementation task group, with that group's tests in
> the same PR — **not** one large PR at the end. Checkpoint-only groups are gates you run before
> opening a PR, not PRs themselves. **Hard stop when this spec is complete**: report and wait.
> Full rule in [`../HANDOFF.md`](../HANDOFF.md).

**Read `requirements.md` and `design.md` first.** Requirement numbers (R1–R13) are referenced
throughout.

**Prerequisite:** spec 1 (`offline-switching-core`). Spec 3 (perception) is helpful but not
required — these can be built against scripted `WorldState` sequences.

**The rule that governs this whole spec:** directors are advisory. Nothing here gets a write path
to the switcher, and `execution/controller.py` is not modified. If a task seems to need a
controller change, stop and raise it.

---

## Task Group 1: Dependencies and the policy contract (R3)

- Add an `llm` optional-dependency group to `pyproject.toml`: `ollama`, `anthropic[bedrock]`.
- `director/policy.py`: `Segment`, `ShotType`, `CutTempo` enums and the `ShotPolicy` model, with
  a short free-text rationale field for logging.
- Define `DEFAULT_POLICY` — what the local tier uses before any cloud response has arrived. The
  system must never block waiting for a policy (R2).

## Task Group 2: Async runner (R5)

- `director/async_runner.py`: the skip-if-busy wrapper.
- On each call: if a request is already in flight, **skip** — do not queue — and return the
  previous result. Otherwise fire asynchronously and return the previous result.
- Apply a timeout; on expiry abandon the call and keep the previous decision.
- Build this **before** either LLM tier. Both depend on it, and doing it first prevents someone
  writing a blocking call "just to get it working" that then stays.

## Task Group 3: Checkpoint

- Test with a stub that sleeps: assert calls are skipped while one is in flight, and that
  wrapper latency stays flat regardless of stub delay (property 2).

## Task Group 4: Local tier (R4, R6, R7)

- `director/local_llm.py`: `LocalLLMDirector` talking to `ollama serve` over HTTP.
- Pass `format=shot_intent_schema(camera_ids)` so output is grammar-constrained and the `camera`
  field carries an enum of only the cameras actually present (R4). This is what makes it
  structurally impossible for the model to name a camera that isn't connected.
- `temperature=0`, `num_predict=128` (caps a rambling generation), **`keep_alive=-1`**.
- ⚠️ `keep_alive=-1` matters: without it the model unloads after a few minutes idle and the next
  call pays a multi-second reload exactly when a decision is needed.
- Prompt order: stable system + schema **first**, volatile `WorldState` **last** (R9).
- Default model: a 4B-class model, 4-bit quantized (~3 GB resident). Do not default to 8B — on a
  16 GB M1 Pro also running capture and CV, that risks swap, and swap is fatal to frame timing.
- If the server is unreachable: degrade to the rules director and log **once**, not per tick (R6).

## Task Group 5: Cloud tier (R8, R9, R10)

- `director/cloud_llm.py`: `CloudPolicyDirector` returning a `ShotPolicy`.
- Client: `AnthropicBedrockMantle(aws_region=...)`. The region is **required** — there is no
  default.
- Model ID: **`anthropic.claude-opus-5`**. Bedrock IDs carry the provider prefix; the bare
  first-party ID errors.
- Structured output via `output_config={"format": {"type": "json_schema", "schema": ...}}` with
  `"effort": "low"`.
- ⚠️ **Put an explicit `cache_control` breakpoint on the last system block.** Bedrock does not
  support automatic top-level cache control, so without this nothing caches and every call is
  full price.
- ⚠️ The stable prefix must be **byte-identical** between calls. No timestamps, no UUIDs, no
  service IDs in the system block. This bug is silent — it just makes everything cost 10× more.
- Do not reach for Batches, the Files API, or task budgets; none are available on Bedrock.
- Log input, output, and **cache-read** token counts per call (R10).

## Task Group 6: Checkpoint

- All tests use **stubs** — no test may require a running Ollama or real AWS credentials (R12).
- Prefix-stability test: build requests from two different `WorldState`s and assert the system
  block is byte-identical. Cheapest possible guard against the silent cache bug.
- Malformed-response test: assert a validation failure retains the previous decision unmodified
  (property 5).

## Task Group 7: Layered director (R1, R2, R11)

- `director/layered.py`: `LayeredDirector` composing rules + local + cloud behind the single
  `Director` protocol, so `app.py` sees one object.
- Cloud policy on a slow cadence biases the local tier; local emits the `ShotIntent`; rules is
  the fallback when anything above it is unavailable.
- Record which tier produced each decision and include it in the `Decision` log (R11). Without
  this you cannot tell afterwards whether the models helped.
- Wire mode/config so each tier can be independently disabled.

## Task Group 8: Checkpoint

- **Offline test**: no Ollama, no AWS credentials, no network — assert the app still produces a
  valid decision every tick via rules (property 3, R12).
- **No-write test**: pass a switcher whose every method raises, run all three directors, assert
  nothing raises (property 1). This is the safety-boundary test; do not skip it.
- Confirm `execution/controller.py` is unmodified: `git diff --stat` shows no changes to it (R13).

## Task Group 9: Tests

- Every requirement R1–R13 maps to at least one named test.
- Assert all six correctness properties from `design.md`.
- Assert every returned `ShotIntent` names a camera present in the source `WorldState`
  (property 4).

## Task Group 10: Coverage checkpoint

- Global 85%. `director/` at 90% — these are the modules whose failure modes are silent.

## Task Group 11: Comparative evaluation

This is the group that answers whether the spec was worth building.

- Replay one recorded `WorldState` sequence through all three configurations: rules alone,
  rules + local, and all three tiers.
- Diff the cut lists. Report where they disagree and which looks better.
- If the local tier does not beat `rules`, say so plainly in the PR. That is a legitimate and
  useful outcome, and much better discovered here than on air.

## Task Group 12: Runtime verification

- A real run against a live `ollama serve` and real Bedrock credentials, in **`shadow` mode**.
- Capture the decision log with tier attribution and the token-usage report.
- Confirm `cache_read_input_tokens` is non-zero after the first call. If it is always zero, the
  stable prefix is varying — find it before merging.

## Task Group 13: Submit for review

- Branch first; `main` is protected.
- Commit, open a draft PR using the template, include the comparative evaluation and the token
  report.
- **No secrets committed** — credentials come from the environment or the AWS credential chain
  (R13).
- **Clean up**: `git status` shows only intended deliverables.
