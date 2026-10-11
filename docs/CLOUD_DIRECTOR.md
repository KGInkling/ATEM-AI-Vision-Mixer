# Bedrock policy guidance

`director/cloud_llm.py` implements LLM-directors task group 5.
`CloudPolicyDirector.update(state) -> ShotPolicy` returns advisory strategic
guidance. It selects no camera, owns no switcher, and does not change the
controller. Layered application wiring belongs to the next implementation group.

## Supported route and configuration

The approved model is Sonnet 4.6 through `AnthropicBedrock` on Bedrock Runtime.
The original Opus 5/Mantle combination cannot provide the required structured
JSON output. See [AWS structured outputs](https://docs.aws.amazon.com/bedrock/latest/userguide/structured-output.html)
and the [Sonnet 4.6 model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-anthropic-claude-sonnet-4-6.html).

Both `aws_region` and `inference_profile` are required. Supported profile IDs are
`us.anthropic.claude-sonnet-4-6`, `eu.anthropic.claude-sonnet-4-6`,
`jp.anthropic.claude-sonnet-4-6`, and `global.anthropic.claude-sonnet-4-6`.
Choose routing deliberately and verify model availability and invocation
permissions before a separately authorized live run. The adapter does not verify
those account-specific conditions or change AWS resources.

An optional `aws_profile` selects an existing credential profile. Otherwise the
SDK uses its AWS credential chain. Credentials belong only in AWS authentication,
never prompts or logs. The client targets the explicit region's
`https://bedrock-runtime.<region>.amazonaws.com` endpoint, ignores environment
proxies, does not follow redirects, and disables SDK retries. Least-privilege
invocation permissions remain an operator responsibility.

The declared `llm` extra requires Anthropic 1.13.0 or later within major version 1.
The offline SDK proof used Anthropic 1.13.0, HTTPX2 2.13.1 and Pydantic 2.13.4 in
a disposable environment. SDK imports occur only inside the worker; constructing
the adapter requires neither SDKs nor credentials.

## Request and privacy

Each call uses `messages.parse(output_format=ShotPolicy)`, `max_tokens=1024`,
and `output_config={"effort": "low"}`. The SDK transforms the schema into the
supported wire subset and validates output against the complete original model.
Unsupported wire string bounds become descriptive hints, while the original
nonempty, 240-character rationale bound remains enforced locally. See
[Anthropic structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

The system block and output schema remain stable. The final user message contains
the shared `prompt_context.world_state_summary`: timestamp, numeric camera IDs,
live duration, speech/pause/level scalars, per-camera health/presence/scores and
fixed known defect codes. Raw frames, audio, face data, identities, transcripts,
extra fields and arbitrary defect text are excluded. The snapshot is validated
and serialized before submission, so later caller mutations cannot change it.

These observations cannot establish a service topic or identity. The prompt
requires an unknown segment when it is not established. Whether cloud guidance
improves shot selection must be measured in the later comparative evaluation.

The last stable system block has an ephemeral cache checkpoint. Sonnet 4.6 needs
at least 1,024 tokens before that checkpoint; the compact prompt may be below this
minimum. No filler is added. Zero cache reads can mean an ineligible prefix,
expiry, or prefix changes; real cache hits are a later live verification.

## Cadence, retention and cleanup

The default submission interval is 25 seconds; configuration must be finite and
at least 20 seconds. Results are polled every tick independently of submission
cadence. A due attempt advances the schedule even when the worker is busy, so
there is no request queue or immediate retry when a slow call completes.

Until a timely validated response arrives, updates return `DEFAULT_POLICY`.
Malformed output, refusals with no parsed policy, missing SDKs, authentication or
transport errors retain the previous/default policy. Error logs contain only
the exception type. Policies are immutable. Every actual invocation has a log
entry; numeric input/output/cache-read counts are logged before policy validation,
so invalid policies do not hide returned usage. Response text is never logged.

The default logical deadline is 10 seconds, with the same finite I/O timeout.
I/O timeouts bound inactivity rather than total wall time. The shared runner
discards late responses but keeps the occupied slot until the call physically
ends. `close()` stops new work without waiting; Python process exit still waits
for running executor threads. See [runner lifecycle](ASYNC_RUNNER.md).

```python
from atem_ai_vision_mixer.director.cloud_llm import CloudPolicyDirector

# Illustrative explicit routing; using update may invoke AWS and incur cost.
director = CloudPolicyDirector(
    aws_region="us-east-1",
    inference_profile="us.anthropic.claude-sonnet-4-6",
    aws_profile="rehearsal",
)
try:
    policy = director.update(world_state)  # validated caller-owned observation
finally:
    director.close()
```

## Offline proof and remaining checks

The existing integration test owner covers cadence boundaries, prompt stability,
privacy, validation/retention, slow workers, missing SDKs, usage logs, explicit
routing and cleanup. All use stubs without model services or AWS credentials:

```bash
.venv/bin/python -B -m pytest -p no:cacheprovider tests/test_integration.py -q
```

A separate real-SDK MockTransport proof verified the signed request body,
transformed schema, complete policy validation, usage logging before malformed
policy rejection, finite timeout extensions, zero retries, no redirects/proxies,
and closed clients. It supplied fake signing keys, blocked credential-provider
discovery and socket connections, and made no AWS calls.

Live invocation permissions, latency, actual cache eligibility/hits, costs,
comparative quality and application activation remain later checkpoints. This
adapter can be reverted as one standalone change before application wiring lands.
