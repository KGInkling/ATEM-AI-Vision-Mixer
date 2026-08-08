# Repository rulesets

The JSON files in this directory are the reviewable source for repository rulesets. GitHub
assigns each live ruleset an ID when it is created; that ID is not part of the create request.

## `main-protection`

- GitHub ruleset ID: `20595499`
- Live ruleset: <https://github.com/KGInkling/ATEM-AI-Vision-Mixer/rules/20595499>
- Enforcement: `active`
- Created: August 8, 2026

Use the ID to inspect the live configuration:

```bash
gh api /repos/KGInkling/ATEM-AI-Vision-Mixer/rulesets/20595499
```

The live configuration and [`main-protection.json`](main-protection.json) must stay in sync.
