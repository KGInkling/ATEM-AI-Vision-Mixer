# Advisory shot policy

`director/policy.py` defines the guidance a cloud director will eventually send
to the local director. It describes a service segment, preferred framing, and
relative cutting pace. It neither chooses a camera nor commands a switcher.
The existing `ShotIntent` still represents one camera suggestion.

This is LLM-directors task group 1. Model clients, scheduling, policy replacement,
and application wiring belong to later groups. The existing `llm` dependency
group already declares `ollama` and `anthropic[bedrock]`; the policy module imports
only Pydantic and the standard library, so it works without either SDK installed.

## Fields and vocabulary

Every field is required; extra fields are rejected.

| Field | Allowed values |
|---|---|
| `segment` | `unknown`, `sermon`, `worship`, `prayer`, `announcements`, `transition` |
| `preferred_shot` | `wide`, `medium`, `tight` |
| `cut_tempo` | `slow`, `normal`, `fast` |
| `rationale` | Text, trimmed at both ends, 1–240 characters after trimming |

`unknown` means the segment has not been established. A shot type is a framing
preference, not a camera ID or PTZ command. Tempo is relative guidance, not a
dwell time or permission to override a controller veto.

`DEFAULT_POLICY` uses `unknown`, `wide`, and `slow`, with a short explanation.
This conservative startup guidance is available immediately, before any cloud
response. All policies are immutable so a caller cannot accidentally change a
shared default or a previously retained policy.

## Validation and schema

```python
from atem_ai_vision_mixer.director.policy import DEFAULT_POLICY, ShotPolicy

policy = ShotPolicy.model_validate_json(
    '{"segment":"sermon","preferred_shot":"tight","cut_tempo":"slow",'
    '"rationale":"Favor tight shots at sentence pauses."}'
)
schema = ShotPolicy.model_json_schema()
print(policy.model_dump_json())
print(DEFAULT_POLICY.model_dump_json())
```

Pydantic exposes enum choices, required fields, rationale length bounds, and
`additionalProperties: false` in the JSON Schema. Unknown enum values, absent or
extra fields, blank rationale, and oversized rationale raise `ValidationError`.
Assignments also raise `ValidationError` without changing the policy. A future
client must validate a candidate before replacing its previous policy; this
module does not own that state or make network requests. Compatibility with each
model runtime's schema interface will be verified when its client is implemented.

Run the director-contract tests without models, credentials, or hardware:

```bash
.venv/bin/python -B -m pytest -p no:cacheprovider tests/test_intent.py -q
```
