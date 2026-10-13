# ATEM-AI-Vision-Mixer
An AI video switcher that picks the best shots for video feed

## Development

See [Automatic Claude review](docs/CLAUDE_REVIEW.md) for draft-PR scheduling,
model-asset checks, and recovery from a failed review.

See [Docker development environment](docs/DOCKER.md) for reproducible builds, tests, and the
boundary between portable logic and host-only video hardware.

See [Synthetic multiview footage](docs/MULTIVIEW.md) to generate 1080p 4-up/7-up recordings
and crop configurations for testing perception without hardware.

See [Feed health and motion](docs/FEED_HEALTH.md) for local black/frozen-feed
detection, quality defects, and the four-camera performance checkpoint.

See [Speech detection and pause grading](docs/AUDIO_VAD.md) for local ONNX speech
detection, its audio input contract, and pause timing.

See [Local people detection](docs/PEOPLE.md) for MediaPipe model setup,
per-camera tracking, and pose limited to the live camera and challenger.

See [Framing quality and movement](docs/FRAMING.md) for normalized composition
features, named defects, and local per-camera movement history.

See [Recorded perception pipeline](docs/PIPELINE.md) to turn captured video and
audio into validated WorldState snapshots without hardware.

See [Perception verification](docs/PERCEPTION_VERIFICATION.md) for the requirement
map, recorded golden sequence, and opt-in local timing checkpoints.

See [Advisory shot policy](docs/SHOT_POLICY.md) for the validated guidance contract
and conservative startup default used by the forthcoming LLM directors.

See [Nonblocking model requests](docs/ASYNC_RUNNER.md) for the single-worker runner,
request deadlines, retained results, and cleanup requirements.

See [Local Ollama shot suggestions](docs/LOCAL_DIRECTOR.md) for schema-constrained
advice, safe prompt data, and rules fallback when the local model is unavailable.

See [Bedrock policy guidance](docs/CLOUD_DIRECTOR.md) for the Sonnet 4.6 adapter,
explicit AWS routing, slow cadence, policy retention, and offline validation.

See [Layered camera advice](docs/LAYERED_DIRECTOR.md) for application model opt-ins,
independent tier cadences, accurate decision attribution, and offline defaults.

See [Director verification](docs/DIRECTOR_VERIFICATION.md) for the R1–R13 test map,
controller safety matrix, cost-log capture, and remaining live-model checkpoints.

See [Recorded director comparison](docs/DIRECTOR_EVALUATION.md) for a rules-only
reference report, explicit model comparisons, cut differences, and quality limits.

## Emergency main-branch access

Normal changes must reach `main` through a pull request. The `main-protection` ruleset has no
admin bypass, including for the repository owner. Use this escape hatch only when an urgent
incident cannot wait for the pull-request path.

The deliberate, auditable escape hatch is to disable ruleset `20595499`, make the emergency
push, and immediately reactivate it:

```bash
gh api --method PUT \
  /repos/KGInkling/ATEM-AI-Vision-Mixer/rulesets/20595499 \
  -f enforcement=disabled \
  --jq .enforcement

git push origin main

gh api --method PUT \
  /repos/KGInkling/ATEM-AI-Vision-Mixer/rulesets/20595499 \
  -f enforcement=active \
  --jq .enforcement
```

The first API command must print `disabled`, and the last must print `active`. Run the
reactivation command even if the push fails. Never leave protection disabled while diagnosing
the incident, and never add a bypass actor as a shortcut. Finally, inspect the
[live ruleset](https://github.com/KGInkling/ATEM-AI-Vision-Mixer/rules/20595499) and confirm its
configuration still matches the [committed ruleset](.github/rulesets/main-protection.json).
GitHub documents the update operation in its
[repository rulesets API](https://docs.github.com/en/rest/repos/rules#update-a-repository-ruleset).
