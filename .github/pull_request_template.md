## Workflow identity

<!-- Bind the change and every reported result to one reviewable revision. -->

- Workflow ID:
- Risk tier (`green`, `yellow`, or `red`):
- Exact head SHA:
- Base branch:
- Reviewer-ready commit range:
- Affected contracts:

- [ ] The exact head SHA above matches the current pull request head.

## What changed and why

### What changed

<!-- Summarize the behavior, configuration, or documentation changed by this pull request. -->

### Why

<!-- Explain the problem this solves and why this change is needed now. -->

## How it was tested

<!-- List the exact commands, scenarios, or manual checks that were run. -->

- [ ] Tests were added or updated for changed behavior.
- [ ] All relevant local and CI checks pass.
- [ ] Testing is not applicable, with an explanation below.

Testing details:

## Coverage

<!-- Record the reported values when executable code changes. -->

- Total coverage:
- Changed-line coverage:
- Safety-controller coverage, if applicable:

Select every statement that applies:

- [ ] The required coverage gates pass.
- [ ] Coverage is not applicable because no executable code changed.
- [ ] A coverage exception is necessary and is explained below.

Coverage details or exception:

## Backward compatibility

Select one:

- [ ] This change is backward compatible.
- [ ] This change is not backward compatible; migration steps and affected users are described below.

Compatibility details:

## Rollback plan

<!-- Describe how to restore the last known-good state. Include configuration or data recovery steps when needed. -->

- [ ] The rollback steps below are concrete and have been checked.
- [ ] No rollback action is needed, with an explanation below.

Rollback steps or explanation:

## Runtime verification

<!-- Provide evidence from actually running the change, not only from reading the diff. -->

- Environment:
- Evidence, logs, or screenshots:

Select one:

- [ ] The changed behavior was verified at runtime.
- [ ] Runtime verification is currently blocked or not applicable, with an explanation below.

Runtime verification details:

## Check and review evidence

<!-- Use pending, passed, failed, or unavailable. Missing or stale evidence is unavailable, never passed. -->

| Evidence | Status | Exact SHA or unavailable reason |
|---|---|---|
| Required GitHub checks |  |  |
| Code review |  |  |
| Release readiness |  |  |
| Session AI |  |  |
| Human ready/merge decision |  |  |

- [ ] Every result above belongs to the exact head SHA recorded in this template.
- [ ] Missing, stale, or unverifiable results are marked unavailable.

## Rollout plan

<!-- State what becomes active on merge, how it is verified, and any later activation boundary. -->

- [ ] The rollout steps and post-change verification are concrete.
- [ ] This pull request has no live activation, with an explanation below.

Rollout steps or explanation:

## Unavailable evidence

<!-- Name unavailable checks, environments, hardware, accounts, or external owners and their impact. -->

Unavailable evidence and resulting blocker or limitation:

## Final review checklist

- [ ] The pull request is limited to one coherent task group.
- [ ] The implementation follows the approved requirements and design.
- [ ] Failure behavior and operational risk were considered.
- [ ] Documentation was updated where users or operators need it.
- [ ] External activation or mutation has separate explicit authorization.
- [ ] No secrets, credentials, logs, generated media, or scratch files were committed.
