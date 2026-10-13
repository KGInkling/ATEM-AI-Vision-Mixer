# Recorded director comparison

`tools/evaluate_directors.py` provides the comparative evaluation tool for
LLM-directors group 11. It reads the same anonymous recorded observations for
each selected configuration, runs the existing layered director and deterministic
controller on a fake switcher, and reports cut-list differences. It never opens
capture hardware or changes a real switcher.

## Offline reference

Run from the repository checkout with the core dependencies installed:

```bash
.venv/bin/python -B -m tools.evaluate_directors \
  tests/golden/perception.jsonl > comparison.json
```

The default selects rules only. It imports no model SDK, reads no AWS credential
chain and makes no network call. The committed fixture has 20 snapshots over
1.9 seconds, using controlled perception predictions and a recorded-file
regression. It is smoke input, not a representative service or model-quality
benchmark. Its rules reference produces no cuts; models are not requested.

The entire JSONL input is validated before any selected model can run. Blank
lines are ignored; snapshots must be finite with strictly increasing timestamps
and nonnegative initial live duration. Existing `WorldState` validation and the
shared explicit scalar/fixed-defect projection discard private extras and
arbitrary defect text. Error messages do not echo input contents or filenames.
The report identifies input by SHA-256, snapshot count and time span.

## Model comparisons

After a separately authorized rehearsal is configured, `--enable-local` adds the
local variant, and `--enable-cloud` adds the cloud-informed variant. Cloud
comparison requires local advice plus an explicit AWS region/inference profile.
Optional `--aws-profile` selects an existing credential profile:

```bash
.venv/bin/python -B -m tools.evaluate_directors /path/to/world-state.jsonl \
  --enable-local --enable-cloud --aws-region us-east-1 \
  --inference-profile us.anthropic.claude-sonnet-4-6 --aws-profile rehearsal \
  > comparison.json 2> usage.log
```

These flags permit real model calls and Bedrock cost. This implementation was
verified offline only; no such call was made. Use a known representative input
and authorize its run beforehand. Existing transport limits, output budgets,
schemas, privacy filtering and disabled cloud retries remain in force.

Rules replay runs quickly. Each model variant is separately paced in wall time
to recorded timestamps, so two model variants take approximately two recording
durations, plus processing/remaining worker cleanup. No evaluation code waits on
a model future inside a control tick. The existing runner polls advice, skips
busy submissions and discards late results. The controller receives a freshly
sampled processing time after advice, preserving its stale-observation veto.

## Program simulation

Each run starts with the recording's first program camera and live duration.
Later program state comes from that run's fake switcher and elapsed time since
its own cuts. Reusing the original recorded dwell after a hypothetical cut would
otherwise allow a second cut too early.

Later recorded bus changes are not replayed as human operator actions. The input
does not distinguish operator takes from other program changes. This is a
counterfactual AI-only simulation; human-takeover behavior is proved separately
by the existing scenario tests. Camera/audio observations remain fixed and
identical across runs. Perception histories and selected-camera pose analysis
are not recomputed after hypothetical cuts, which limits closed-loop fidelity.
`fake_auto_simulation` in the report refers only to the in-memory switcher.

Adapter shutdown is nonblocking. A request still finishing at a variant's end
can overlap the next variant; this tool does not establish performance isolation.
Use isolated processes for any later latency or contention benchmark.

## Reading the report

| Field | Meaning |
|---|---|
| `runs` | Rules plus explicitly selected variants; each has cut records, actual tier/reason counts and stale-snapshot count |
| `cuts` | Snapshot index, recorded time, actual processing time, camera and source tier |
| `model_evidence` | `not_requested` for rules; `valid_advice` when the requested tier actually supplied advice; otherwise `no_valid_advice` |
| `differences` | Matching cuts and cuts unique to rules or the variant, aligned by snapshot/camera rather than tier or wall-clock jitter |
| `quality` | Always `unassessed`; cut disagreement does not establish better visual direction |

Missing SDKs, unavailable services, expired responses or a short recording can
leave a requested variant using only rules/local advice. The report does not
call this a valid comparison for an unused requested tier. In the short golden
fixture even a valid cloud policy may arrive after the first local submission,
with no later local submission to use it.

No model has demonstrated an improvement over rules in the current offline
evidence. A longer representative recording, actual model participation and
human review of the cut lists against the original footage are still required
to finish group 11's quality judgment. Raw footage stays local and never goes
to an LLM. Live shadow verification remains group 12.

The existing integration test owner covers three real adapter paths with stubbed
SDK outputs, deterministic cut differences, simulated dwell, finite/private input
validation, CLI selection, outages, pacing/late-state vetoes and cleanup. Tests
do not require model services or credentials. Existing app, controller, adapters,
world contracts, perception and the golden fixture remain unchanged.
