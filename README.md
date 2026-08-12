# ATEM-AI-Vision-Mixer
An AI video switcher that picks the best shots for video feed

## Development

See [Docker development environment](docs/DOCKER.md) for reproducible builds, tests, and the
boundary between portable logic and host-only video hardware.

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
