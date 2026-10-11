# Layered camera advice

`director/layered.py` implements LLM-directors task group 7 and the offline/no-write
checkpoint. `LayeredDirector.decide(state) -> ShotIntent` has the existing rules
interface. It combines cloud policy and local shot advice while the deterministic
controller remains the only switcher writer, with every existing veto.

## Configuration

Both model tiers are disabled by default. The application still uses rules with
no SDK, model server, credential or network requirement, and `shadow` remains the
default controller mode:

```bash
.venv/bin/python -B -m atem_ai_vision_mixer.app --scenario sermon
```

The CLI adds independent `--enable-local` and `--enable-cloud` switches. Enabling
local advice uses the existing loopback Ollama adapter; the application never
starts a server or downloads a model. Enabling cloud explicitly permits Bedrock
policy requests and their cost, and requires `--aws-region` and
`--inference-profile`. Optional `--aws-profile` selects an existing credential
profile. Configuration errors are reported before any model request.

For a separately authorized rehearsal with model services configured:

```bash
.venv/bin/python -B -m atem_ai_vision_mixer.app --scenario sermon \
  --enable-local --enable-cloud --aws-region us-east-1 \
  --inference-profile us.anthropic.claude-sonnet-4-6 --aws-profile rehearsal
```

Cloud may be enabled while local is disabled. It then updates policy, but camera
suggestions remain rules because cloud selects no camera. Cloud policy affects
camera suggestions only through an enabled, successful local model request.
See [local setup and privacy](LOCAL_DIRECTOR.md) and
[supported AWS routing and cost boundaries](CLOUD_DIRECTOR.md).

## Cadence and retained advice

Cloud keeps its existing 25-second default cadence and 10-second logical deadline.
Local submission defaults to three seconds; the Python constructor accepts a
positive finite `local_interval_seconds`. Local retains its two-second deadline.
Both clocks use monotonic wall time rather than recorded frame timestamps.

Each control tick polls completed advice. Polling between local submissions can
accept a timely result immediately, reevaluate rules fallback, and reject a
retained camera that has disappeared. A due tick advances the schedule even when
busy, skips its request, and returns retained advice. There is no request queue.
The adapters keep their existing schema validation, privacy projection, transport
controls and failure retention.

The scenario CLI replays as fast as possible, without sleeping. It can finish
before a model response arrives; therefore enabled models may still produce only
rules-attributed records. This replay is a wiring/safety check, not a real-time
quality or latency evaluation. Comparative evaluation and a live shadow rehearsal
remain later task groups.

## Attribution and JSONL compatibility

The layered director exposes `tier` for its latest returned suggestion:

| Value | Meaning |
|---|---|
| `rules` | The current rules fallback supplied the suggestion |
| `local` | A retained, valid local model result used `DEFAULT_POLICY` |
| `cloud_informed` | A retained, valid local result used a received cloud policy |

The local worker retains its immutable request policy with the result.
`LocalLLMDirector.policy_used` is that policy when advice is returned, or `None`
when rules are returned. A cloud response arriving while an older local request
runs does not relabel that older result. Configuring a tier, or a model choosing
the same camera as rules, does not establish provenance.

Every app JSONL record now includes `director_tier` beside the original controller
fields. This is an additive log-format change: strict readers must accept or
remove the field. The controller's `Decision` model and `run_scenario()`'s returned
`list[Decision]` are unchanged. Tier describes the input suggestion even when the
controller vetoes it. No raw observations, model rationale or policy text enter
these decision records.

## Cleanup and verification

The application closes both adapters in `finally`, including output failures.
`LayeredDirector.close()` is idempotent; subsequent decisions raise `RuntimeError`.
It does not wait for an occupied worker. Python cannot cancel running threads;
process exit still waits for them, and late results are discarded. See
[runner deadlines and shutdown](ASYNC_RUNNER.md).

The nearest existing test owners prove cadence boundaries, retained-camera checks,
delayed policy attribution, outages/recovery, independent enable switches, missing
SDK operation, CLI configuration, JSONL attribution and cleanup. All switcher
methods are guarded against director access; existing controller safety scenarios
remain intact. Model tests use stubs and real bounded executor futures:

```bash
.venv/bin/python -B -m pytest -p no:cacheprovider \
  tests/test_integration.py tests/test_app.py -q
```

No live Ollama/AWS request or hardware action was performed for this change.
Rollback is a revert of this composition/app task group; no resource migration
or controller change is required.
