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

## Activation candidate

[`main-protection.json`](main-protection.json) declares the proposed required contexts below.
It is not applied automatically. The live ruleset still requires only `lint` and `test` until
the separately authorized activation; this temporary difference is the planned migration.

| Context | Evidence source |
|---|---|
| `lint` | CI lint job |
| `test` | CI test and coverage job |
| `build` | Source/wheel build and clean-install job |
| `integration` | macOS perception integration job |
| `dependency-review` | GitHub dependency-review job |
| `CodeQL` | GitHub Advanced Security's CodeQL result |
| `claude-review` | Manual workflow's commit status and marked review on the exact PR head |

These names were observed during the shadow checkpoint, but those earlier passes do not
authorize activation. Verify them again on the activation PR's unchanged final head. The
`Analyze (actions)` and `Analyze (python)` jobs are diagnostic evidence alongside the `CodeQL`
result. Keep both analyses passing.

### Before the live switch

1. Keep the final activation PR in draft. Run the manual reviewer on its frozen head and verify the
   marked review and commit status, including the manual workflow run URL.
2. The initial candidate received a marked clean manual review on
   [PR #25](https://github.com/KGInkling/ATEM-AI-Vision-Mixer/pull/25#pullrequestreview-5373652239).
   That prerequisite permits retiring `.github/workflows/claude-code-review.yml` in this
   revision. The on-demand `.github/workflows/claude.yml` remains. The retired CheckRun shared
   the `claude-review` name and must not be counted as the manual status.
3. After any commit changes the head, rerun CI, CodeQL, and the manual reviewer. Every proposed
   required context must pass together on the final unchanged head. Do not combine results from
   different revisions, and do not mark ready or merge during this checkpoint.
4. Complete the required review streams and finding dispositions. Missing or stale evidence is
   unavailable, not clean. Obtain explicit approval for the live ruleset change.
5. Read the live ruleset again. Update only its required-context list, preserving every other
   live setting, including server-added review parameters. Keep enforcement active, strict
   checks enabled, and bypass actors empty. Never disable protection during the switch.
6. Read back the applied ruleset and compare it with the approved candidate. Verify that a
   controlled failing required context blocks merging, then restore that context to its genuine
   passing result. Include this enforcement test in the live-change approval.

GitHub accepts both CheckRuns and commit statuses as required checks; the context name is not
the workflow display title. See [required status checks](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets#require-status-checks-to-pass-before-merging).

### Rollback

Retain the pre-activation live ruleset snapshot. With explicit approval, restore only the
previous required-context list, `lint` and `test`, without disabling enforcement or changing
any other protection. Reconcile the committed JSON through a reviewed revert. The manual
workflow can remain available on demand; restoring the automatic reviewer is not needed to
restore the prior merge requirements.

After activation or rollback, the committed desired configuration and live required contexts
must agree. Record the exact head, checks, approval, and readback in the activation PR.
