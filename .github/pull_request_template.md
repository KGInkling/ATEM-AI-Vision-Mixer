## Summary

<!-- In 2–3 sentences, explain the problem and resulting behavior. -->

## Validation

<!-- List exact commands or scenarios and their results. Explain anything not applicable. -->

- Checks and results:
- Runtime verification (environment and evidence):
- Unavailable evidence and its impact:

## Risk and recovery

<!-- Keep routine changes brief; include migration or recovery steps when needed. -->

- Backward compatibility:
- Rollout and post-change verification (include any separate activation):
- Rollback:

## Review details

<details>
<summary>Scope, coverage, and review evidence</summary>

### Revision

- Workflow ID:
- Risk tier (`green`, `yellow`, or `red`):
- Review cycle (`1`, `2`, or `3`; pending before Review starts):
- Review profile (`core` or `full`):
- Exact head SHA:
- Base branch and SHA:
- Reviewer-ready commit range:
- Scope fingerprint:
- Affected contracts:

### Scope

- Changed paths:
- Explicit exclusions (including unrelated or uncommitted work):

### Reviewer walkthrough

<!-- Give an ordered path through the relevant files and proof. -->

1. Owner and intended behavior:
2. Trust, state, or compatibility boundary:
3. Failure handling and recovery:
4. Regression and runtime evidence:

### Coverage

<!-- Record measured values and gate results, or explain why coverage is not applicable. -->

- Total / changed-line / safety-controller coverage (if applicable):
- Coverage gate results or exception:

### Check and review evidence

<!-- Use pending, passed, failed, or unavailable. Missing or stale evidence is unavailable. -->

| Evidence | Status | Exact SHA or unavailable reason |
|---|---|---|
| GitHub checks |  |  |
| CodeQL (`actions` and `python`) |  |  |
| Manual general code review |  |  |
| AWS release readiness |  |  |
| Session AI (`core` or `full`) |  |  |
| Human ready/merge decision |  |  |

### Checklist

- [ ] Scope is one committed task; unrelated, sensitive, and scratch files are excluded.
- [ ] Behavior matches approved requirements; needed documentation is updated.
- [ ] Tests, coverage, and runtime checks have results or explained limits.
- [ ] Compatibility, rollout, and rollback are documented.
- [ ] Every result matches the current PR head; missing, stale, or unverifiable evidence is unavailable.
- [ ] The PR stays draft until review and finding disposition are complete.
- [ ] External activation and ready/merge each require separate human authorization.

</details>
