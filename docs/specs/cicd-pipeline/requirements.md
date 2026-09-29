# Requirements: CI/CD Pipeline and Review Process

Spec 2 of 5. Delivers continuous integration, an Amazon-shaped code review process, Docker for
reproducible builds, and a staged promotion pipeline.

**The finding this whole spec rests on:** `github.com/KGInkling/ATEM-AI-Vision-Mixer` is a
**public** repository on GitHub Free. The enforcement features below — rulesets, required status
checks, CODEOWNERS, Environments with required reviewers *and* wait timers, unlimited macOS
Actions minutes — are free on public repos and **unavailable on a Free private repo**. Notably,
even paying $4/month for GitHub Pro does *not* restore required reviewers or wait timers on a
private repo; those stay public-only until Enterprise.

One staging feature is not available: GitHub's API rejects ruleset `evaluate` enforcement on
this plan as Enterprise-only. The ruleset is therefore created as `active` after the CI jobs have
passed on the two bootstrap pull requests. See `design.md` for the plan matrix and rationale.

---

## Requirement 1: Continuous integration on every pull request

CI SHALL run on every pull request targeting `main` and on every push to `main`.

WHEN a pull request is opened or updated
THEN a workflow SHALL run linting, the full test suite, and the coverage gates, and SHALL report
a pass/fail status check per job.

WHEN a status check named by the active ruleset fails
THEN the pull request SHALL NOT be mergeable.

WHEN an evidence-only check fails or is unavailable before activation
THEN the modernization task SHALL NOT be considered complete and that context SHALL NOT be added
to branch protection, even though the current ruleset does not yet enforce it.

CI SHALL complete in under 5 minutes for the offline switching core, so it does not discourage
small pull requests.

### Requirement 1a: Deterministic evidence expands before enforcement

CI SHALL build both the source distribution and wheel, install the wheel in a clean environment,
and import the installed package from outside the repository checkout.

WHEN a pull request changes declared dependencies
THEN a pull-request-only dependency review SHALL fail for moderate-or-higher vulnerabilities in
runtime dependencies.

WHEN the macOS integration job runs
THEN it SHALL install the `perception` and `dev` dependency groups, run the full test suite, and
report skip reasons.

WHEN the PyAV-backed recorded-media integration test is collected on macOS
THEN it SHALL NOT skip merely because PyAV or NumPy was not installed.

Every third-party action used by `ci.yml` SHALL be pinned to a reviewed full commit SHA with a
readable release comment. Moving major-version tags are discovery inputs, not executable pins.

New deterministic checks SHALL coexist with the currently required `lint` and `test` contexts
until the final activation task. No new context SHALL be added to branch protection piecemeal.

Immediately before live activation, every context intended for the new required list SHALL be
terminal and successful together on the unchanged activation pull request's exact head SHA.
Evidence from different pull requests, an earlier SHA, or a run before a workflow changed SHALL
NOT satisfy this requirement. Missing, stale, or disjoint evidence SHALL block activation.

WHEN a new context is proposed for branch protection
THEN that activation SHALL be a separate reviewed change using only the context name observed on
that unchanged activation head.

## Requirement 2: `main` is protected

The repository SHALL be configured so that changes reach `main` only through a pull request.

WHEN anyone — including the repository owner — attempts `git push origin main`
THEN the push SHALL be rejected.

WHEN a pull request has unresolved review conversations
THEN it SHALL NOT be mergeable.

WHEN a pull request's required status checks are not green
THEN it SHALL NOT be mergeable.

The ruleset SHALL have an **empty** `bypass_actors` list. The owner SHALL NOT be exempt.

WHEN a genuine emergency requires bypassing
THEN the documented escape hatch SHALL be flipping the ruleset's `enforcement` field to
`disabled` and back — a deliberate, auditable act — rather than an invisible admin bypass.

## Requirement 2a: Merged branches are cleaned up

The repository SHALL NOT accumulate stale feature branches.

WHEN a pull request is merged
THEN its head branch SHALL be deleted automatically on the remote, without anyone remembering to
do it.

WHEN an implementing agent's pull request is merged
THEN it SHALL also delete its **local** copy of that branch — the remote setting does not touch
local clones.

WHEN a pull request is closed **without** merging
THEN its branch SHALL NOT be deleted automatically. The work may still be wanted, and a closed
PR is not the same signal as a merged one.

## Requirement 3: Review discipline without a second reviewer

GitHub does not permit a pull request author to approve their own pull request, at any plan
level. A solo developer therefore cannot require an approving review without permanently
deadlocking every pull request.

The ruleset SHALL set `required_approving_review_count: 0` and
`require_code_owner_review: false`, and SHALL still enforce the pull request, status check,
conversation resolution, and linear history requirements.

WHEN a collaborator is added later
THEN raising the approval count to 1 SHALL be a single-field change to the ruleset.

### Requirement 3a: Manual review is exact, visible, and fail-closed

The repository SHALL provide one manually dispatched general code-review workflow with scalar
pull-request-number and expected-head-SHA inputs.

BEFORE any model credential is available or any status is written
THEN the workflow SHALL reject malformed input, a non-default workflow ref, a closed or non-draft
pull request, a fork, a non-default base, a stale head SHA, and a duplicate marked review.

The model job SHALL read one frozen same-repository draft pull request. It SHALL use trusted
base-branch instructions, keep the exact head in a separate directory, consume a precomputed diff,
and receive no repository or GitHub write capability.

The model job SHALL NOT edit files, execute project code or configuration, run tests or builds,
access credentials, create fixes, or publish comments, reviews, or statuses.

WHEN GitHub runner debug logging is enabled
THEN the model job SHALL stop before the Claude credential is supplied. The action step SHALL also
override `ACTIONS_STEP_DEBUG=false` so full model/tool output cannot enter public logs.

WHEN the frozen diff includes a binary, symlink, submodule, sensitive filename or directory,
non-allowlisted extension, image/audio/video/transcript/generated-media path, or high-confidence
credential marker
THEN context preparation SHALL stop before model use and report that privacy boundary unavailable.

The model SHALL receive only trusted base instructions, workflow-generated metadata and diff, and
a sanitized full snapshot of safe allowlisted tracked text for caller/contract tracing, supplied
directly as context with filesystem, command, and MCP tools disabled. It SHALL NOT receive
`pr-head/` access. Credential-marker scanning SHALL cover the entire supplied context, including
unchanged source and trusted instructions. Rename detection SHALL be disabled so both source and
destination paths pass the privacy checks.

WHEN preflight succeeds
THEN a `claude-review` commit status SHALL become pending on the frozen head SHA before model use.

WHEN the model returns schema-valid output for that same base/head pair and the pull request base
and head remain unchanged
THEN a separate no-model publisher SHALL create one marked GitHub review containing reviewer,
reviewed SHA, verdict, mandatory summary, and any consequential findings.

`claude-review=success` SHALL be published only after the workflow re-reads the pull request and
the created review object and verifies its marker, author, commit ID, SHA, and verdict.

`claude-review=success` SHALL attest that review evidence was published and verified; it SHALL NOT
mean the verdict was clean, dispose findings, satisfy human approval, or authorize ready/merge.

WHILE the legacy automatic workflow coexists with the replacement
THEN manual evidence SHALL be verified through the commit-status API and its manual workflow run
URL, not inferred from the legacy CheckRun that shares the `claude-review` display name.

Missing credentials, model failure, timeout, malformed or mismatched output, publication failure,
base or head movement, cancellation, or unverifiable evidence SHALL remain failed, error, pending,
or unavailable; none SHALL be represented as clean.

The old automatic Claude workflow and the on-demand Claude workflow SHALL remain unchanged until
the manual replacement succeeds in a separately authorized shadow checkpoint.

## Requirement 4: An Amazon-style review checklist

The repository SHALL provide a pull request template carrying an explicit review checklist.

The checklist SHALL cover, at minimum: what changed and why, test coverage for the change,
whether the change is backward compatible, how it would be rolled back, and what was verified
at runtime.

The checklist SHALL also record the workflow ID, risk tier, exact head SHA, reviewer-ready commit
range, review cycle and profile, scope fingerprint, affected contracts, exact scope and exclusions,
ordered reviewer walkthrough, check and review states, rollout boundary, and unavailable evidence.

WHEN evidence is missing, stale, or cannot be verified against the recorded head SHA
THEN the checklist SHALL report it as unavailable rather than clean.

The repository SHALL provide a commit-message template that prompts for a specific Conventional
Commit subject, problem, resulting behavior, validation, rollout and rollback, and review identity.
The template is guidance configured per clone; it SHALL NOT add a commit hook or bypass review.

This is the transferable part of Amazon's process. The Builders' Library documents that Amazon
reviewers work from **custom per-team written checklists** evaluating not only correctness but
whether a change can be safely deployed and safely rolled back.

## Requirement 5: Coverage gates

CI SHALL enforce coverage thresholds and SHALL fail the build when they are not met.

WHEN total coverage of the package falls below 85%
THEN the build SHALL fail.

WHEN coverage of `execution/controller.py` falls below 95%
THEN the build SHALL fail. It is the safety layer; an untested branch there is a rule that can
silently fail on air.

WHEN a path in `.coverage-thresholds` does not exist in either the pull request or its base
branch because its scheduled implementation task group has not landed yet
THEN CI SHALL emit a visible pending notice and SHALL activate that file's gate automatically
on the pull request that adds it.

WHEN a thresholded path exists on the base branch but is missing from the pull request
THEN CI SHALL fail, so deleting or renaming a protected file cannot bypass its coverage gate.

WHEN coverage of the lines changed by a pull request falls below 90%
THEN the build SHALL fail.

The patch-coverage gate is the one that matters most day to day: a global floor stops moving
once a codebase is established, whereas a floor on *changed* lines applies pressure to every
pull request.

## Requirement 6: Reproducible builds via Docker

The repository SHALL provide a Docker image that builds the package and runs the full test suite
identically on any machine.

WHEN `docker build` and the containerized test command are run on any Docker host
THEN the tests SHALL produce the same result as on the developer's Mac.

The image SHALL NOT attempt to include the live-capture path.

WHEN a developer reads the Docker documentation in this repo
THEN it SHALL state plainly which layers are containerizable and which are host-only, and why.

## Requirement 7: Docker's hardware boundary is documented, not hidden

The DeckLink Duo 2 is a **PCIe** capture card, and Blackmagic Desktop Video is a macOS system
extension. Docker Desktop on macOS runs containers inside a Linux VM with no PCIe passthrough,
and the Apple Neural Engine is not reachable from a Linux container.

The project SHALL therefore treat the following as **host-native only**, never containerized on
macOS: live DeckLink capture, and CoreML/ANE inference.

The project SHALL treat the following as containerizable: the offline switching core, the
rule-based director, file-based perception, the LLM director tiers, and all tests.

WHEN dependency groups are defined
THEN the capture-only dependencies SHALL be a separate optional group that the Docker image does
not install.

## Requirement 8: Staged promotion pipeline

The repository SHALL define a promotion pipeline modelled on Amazon's source → build → test →
prod flow, using GitHub Environments as the stage gates.

Stages SHALL be: `alpha` (fast checks), `beta` (full integration on the production OS), `gamma`
(hardware-in-the-loop, shadow mode), `prod-onebox` (production machine, assist mode), and `prod`
(production machine, auto mode).

WHEN a stage fails
THEN no later stage SHALL run.

WHEN promoting between stages
THEN the **same build artifact** SHALL be promoted, not rebuilt.

`gamma` and later stages SHALL require hardware and SHALL be marked as blocked until it is
available.

## Requirement 9: Bake time is one service, and promotion is manual

Amazon's bake periods are wall-clock waits because their services take continuous traffic. This
application takes traffic roughly once a week, so a timed bake validates almost nothing — a
quiet Tuesday hour proves only that the app did not crash while idle. Amazon's own bake
conditions include a data-volume clause for exactly this reason.

The bake period for `gamma`, `prod-onebox`, and `prod` SHALL therefore be **one complete
service**, and promotion between them SHALL be manual.

WHEN a stage has been deployed
THEN it SHALL NOT be promoted until the application has run through a full service in that
stage's mode with no alarm condition raised.

The `gamma`, `prod-onebox`, and `prod` stages SHALL each require manual approval from a named
reviewer, using GitHub Environment required reviewers.

WHEN a deployment is waiting for approval
THEN it SHALL be visible in the repository's Deployments view, so a pending promotion is a
tracked state rather than a note in someone's head.

Wait timers MAY be configured as a **floor** — a minimum elapsed time — but SHALL NOT be the
promotion trigger.

## Requirement 9a: Self-hosted runner is narrowly scoped

The self-hosted runner required by `gamma` and later stages SHALL be restricted so that a public
repository cannot be used to execute arbitrary code on the production machine.

WHEN a workflow run is triggered by anyone other than the repository owner
THEN it SHALL require manual approval before any job executes on the self-hosted runner.

The runner SHALL be usable only by the promotion workflow, not by CI.

WHEN the runner is registered
THEN the repository setting requiring approval for outside contributors' workflow runs SHALL be
enabled first.

## Requirement 10: The run-mode ladder is the deployment ladder

The application's existing run modes SHALL be used as the promotion mechanism rather than
inventing a parallel one.

`gamma` SHALL run the application in `shadow` mode, `prod-onebox` in `assist` mode, and `prod` in
`auto` mode.

WHEN `gamma` runs
THEN the deployment SHALL fail if the application performs any switcher write, since shadow mode
must not write.

This maps Amazon's escalating-blast-radius model onto a single-machine application: the thing
that escalates is not how many hosts are affected, but how much authority the AI has.

## Requirement 11: Health-based rollback

GitHub provides no alarm system and no automatic rollback. The project SHALL provide its own.

The project SHALL define a health signal and a rollback script.

WHEN the human operator overrides the AI more than a configured number of times per period
THEN this SHALL be treated as the primary alarm condition — a high takeover rate means the AI is
making bad calls, which is exactly the domain-appropriate equivalent of a rising error rate.

WHEN an alarm condition is met during the bake period
THEN the rollback script SHALL restore the previously deployed version and restart it in
`shadow` mode.

## Requirement 12: Pipeline as code

All CI, pipeline, ruleset, and container configuration SHALL live in the repository and be
version controlled.

WHEN the ruleset is changed
THEN the JSON defining it SHALL be committed, so the configuration is reviewable rather than
existing only in GitHub's web UI.

## Requirement 13: No regressions (SHALL NOT)

This change SHALL NOT modify any file under `atem_ai_vision_mixer/` or `tests/`. It adds CI
around the code; it does not change the code.

This change SHALL NOT make the repository private. Doing so silently removes rulesets, required
status checks, CODEOWNERS, and Environment protection rules on the Free plan.

This change SHALL NOT introduce a dependency on any paid third-party service. Coverage gating
SHALL work without Codecov or any external account.

This change SHALL NOT leave temporary or scratch files in the working tree.
