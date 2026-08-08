# Requirements: LLM Director Tiers

Spec 4 of 5. Adds two model-driven directors on top of the rule-based one from spec 1: a small
local model for shot selection, and AWS Bedrock Claude for slower strategic guidance.

**Depends on:** spec 1 for `WorldState`, `ShotIntent`, and the `Director` protocol. Spec 3
(perception) is not strictly required — these can be developed against scripted `WorldState`
sequences — but real perception output makes the prompts much easier to tune.

**Governing principle:** every director is **advisory**. None of them can touch the switcher.
The deterministic controller from spec 1 remains the only writer and retains every veto.

---

## Requirement 1: Directors are interchangeable

All directors SHALL implement the same `Director` protocol defined in spec 1.

WHEN any director is substituted for another
THEN the controller and application SHALL require no change.

WHEN a director is unavailable or fails
THEN the system SHALL fall back to the rule-based director from spec 1, which has no external
dependencies and always returns an answer.

## Requirement 2: The two tiers do different jobs

The system SHALL run the cloud tier at a slower cadence than the local tier, and they SHALL
produce different kinds of output.

The cloud tier SHALL emit a **`ShotPolicy`** — strategic guidance about the current segment of
the service, valid for tens of seconds.

The local tier SHALL emit a **`ShotIntent`** — a specific camera choice for right now, informed
by the current policy.

WHEN no policy has been received yet
THEN the local tier SHALL operate with a documented default policy rather than blocking.

## Requirement 3: ShotPolicy contract

The system SHALL define a `ShotPolicy` Pydantic model with a closed vocabulary, exposing a JSON
Schema for constraining model output.

`ShotPolicy` SHALL carry at minimum: the current service segment, a preferred shot type, a
cutting tempo, and a short free-text rationale for logging.

WHEN a policy field would take a value outside its enum
THEN validation SHALL fail and the previous policy SHALL be retained.

## Requirement 4: Structured output is schema-constrained, not parsed hopefully

Both LLM tiers SHALL constrain model output to their schema at generation time rather than
parsing free text and hoping.

WHEN the local tier requests a decision
THEN it SHALL pass the `ShotIntent` JSON Schema to the runtime so the output is
grammar-constrained and guaranteed parseable.

WHEN the schema is built
THEN the `camera` field SHALL carry an `enum` of the cameras actually present in the current
`WorldState`, so a model cannot name a camera that does not exist.

WHEN output nonetheless fails validation
THEN the system SHALL log it and reuse the previous decision, never a random or partial one.

## Requirement 5: Directors never block the control loop

LLM calls SHALL be asynchronous and SHALL NOT stall perception or the controller.

WHEN a model call is in flight and the next tick arrives
THEN the new request SHALL be **skipped**, not queued.

A director two decisions behind is worse than no director — stale advice about a shot that is no
longer live is actively harmful.

WHEN a model call exceeds its timeout
THEN it SHALL be abandoned and the previous decision retained.

## Requirement 6: Local tier runs out-of-process

The local model SHALL run in a separate process from the application.

WHEN the application starts
THEN it SHALL communicate with the local model over HTTP rather than loading it in-process.

This keeps model memory outside the Python process and prevents a model load from stalling the
frame loop.

WHEN the local model server is unreachable
THEN the system SHALL degrade to the rule-based director and log it once, not once per tick.

## Requirement 7: Local tier fits a 16 GB machine

The local tier SHALL be configured for an M1 Pro with 16 GB that is simultaneously decoding
1080p video and running CV models.

The default local model SHALL be approximately 4B parameters, quantized.

WHEN the local model is idle between calls
THEN it SHALL remain resident rather than being unloaded and reloaded.

Generation SHALL be capped so a model that rambles cannot stall the tier.

## Requirement 8: Cloud tier uses Bedrock

The cloud tier SHALL call Claude through AWS Bedrock.

WHEN the Bedrock client is constructed
THEN it SHALL be given an explicit AWS region, which is required.

Model identifiers on Bedrock carry a provider prefix and SHALL be written accordingly.

WHEN AWS credentials are configured
THEN they SHALL be least-privilege, scoped to model invocation only.

## Requirement 9: Prompt structure supports caching

Prompts SHALL be ordered so the stable portion forms a cacheable prefix.

WHEN a request is built
THEN the system prompt, schema, and any few-shot examples SHALL come first, and the volatile
`WorldState` SHALL come last.

The stable prefix SHALL be byte-identical between calls. It SHALL NOT contain timestamps, UUIDs,
or any per-request value.

WHEN prompt caching is configured
THEN an explicit cache breakpoint SHALL be placed on the last stable block, because Bedrock does
not support automatic top-level cache control.

## Requirement 10: Cost is bounded and observable

The cloud tier SHALL be called on a fixed slow cadence, not on every perception tick.

WHEN a service runs for its full duration
THEN the number of cloud calls SHALL be bounded by that cadence and SHALL be reported in the
run log.

WHEN token usage is returned
THEN input, output, and cache-read token counts SHALL be logged, so the cost of a service is
knowable rather than estimated.

## Requirement 11: Every decision is attributable

The system SHALL record which director produced each decision.

WHEN a decision is logged
THEN it SHALL name the tier that produced it — `rules`, `local`, or `cloud`-informed — so a
review of a service can tell whether the models helped.

## Requirement 12: Offline and no-credentials operation

The system SHALL run with no local model server, no AWS credentials, and no network.

WHEN neither LLM tier is available
THEN the application SHALL run correctly on the rule-based director alone.

Tests SHALL NOT require a running model server or AWS credentials. Both tiers SHALL be tested
against recorded or stubbed responses.

## Requirement 13: No regressions (SHALL NOT)

This spec SHALL NOT modify `execution/controller.py`. The controller's vetoes apply to LLM
output exactly as they apply to rule-based output, and that is the entire safety argument.

This spec SHALL NOT allow any director to call a switcher method.

This spec SHALL NOT make LLM availability a precondition for the application starting.

This spec SHALL NOT commit AWS credentials, API keys, or any secret. Secrets come from the
environment or the AWS credential chain.

This spec SHALL NOT leave temporary or scratch files in the working tree.
