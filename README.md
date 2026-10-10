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
