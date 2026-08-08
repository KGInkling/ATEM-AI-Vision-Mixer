# Design: CI/CD Pipeline and Review Process

## Approach

Replicate as much of Amazon's deployment model as GitHub natively supports, be explicit about
the three places where it doesn't, and build those by hand.

The central insight that makes the mapping work: **this project's run-mode ladder is already a
deployment ladder.** Amazon escalates blast radius across one-box → AZ → Region → fleet. A
single-machine desktop app has no fleet, so what escalates instead is *how much authority the AI
has over the switcher*: `shadow` (watches, writes nothing) → `assist` (stages on preview, human
takes it) → `auto` (full control). Promotion through those modes is structurally the same
risk-escalation ritual, and it means the pipeline doesn't need a parallel concept invented for it.

### Plan availability — the constraint everything else follows from

Verified against GitHub's own docs source (`data/reusables/gated-features/*.md`), August 2026:

| Feature | Free + **public** (us) | Free + private | Pro + private |
|---|---|---|---|
| Repository rulesets / active branch protection | ✅ | ❌ | ✅ |
| Ruleset `evaluate` mode / Rule Insights | ❌ Enterprise only | ❌ | ❌ |
| Required status checks | ✅ | ❌ | ✅ |
| CODEOWNERS | ✅ | ❌ | ✅ |
| Environments + secrets | ✅ | ❌ | ✅ |
| **Environment required reviewers** | ✅ | ❌ | ❌ |
| **Environment wait timers (bake)** | ✅ | ❌ | ❌ |
| Actions minutes | ✅ unlimited, incl. macOS | 2,000/mo (÷10 on macOS ≈ 200) | 3,000/mo |
| Merge queue | ❌ (org-owned repos only) | ❌ | ❌ |

Three consequences worth internalising. First, **going private is not a $4/month decision** — Pro
still doesn't give required reviewers or wait timers on a private repo, which are the two
features that make the pipeline Amazon-shaped at all. Public is not merely cheaper here; it is
the only configuration where this design works. Second, merge queue is unavailable at any price
because the repo is user-owned rather than organisation-owned. Third, repository rulesets work
on this public Free repo, but their non-blocking `evaluate` mode does not: on August 8, 2026, the
live create-ruleset API returned HTTP 422 and identified that mode as Enterprise-only. The
ruleset must therefore be created as `active` after its status-check names have been proven on
bootstrap pull requests.

### Rulesets, not legacy branch protection

Rulesets are the right choice for a specific reason beyond being the actively developed API: the
bypass model is inverted. Legacy branch protection lets admins bypass **by default** unless you
explicitly set `enforce_admins`. Rulesets exempt **nobody** unless they're named in
`bypass_actors`. For a solo developer trying to impose discipline on themselves, a default of
"the rules apply to you" is the whole point.

Legacy branch protection has a second disqualifier here: its bypass lists only function on
organisation-owned repositories, so on a user-owned repo it degrades to admin-bypass-or-nothing.

### The solo-reviewer problem, and what to do about it

GitHub does not allow a pull request author to approve their own pull request. This is
platform-level, applies to owners and admins, and has no setting to disable. So
`required_approving_review_count: 1` on a one-person repo is a permanent deadlock, and adding
yourself to `bypass_actors` to escape it makes the requirement theatre.

The design therefore sets **`required_approving_review_count: 0`** and keeps everything else
enforced. That still buys: no direct pushes to `main`, required green checks, required
conversation resolution, linear history, squash-only merges, no force-pushes. That is the
Amazon CR discipline minus the second pair of eyes — which is a headcount problem, not a
tooling one.

The escape hatch is deliberately awkward: flip `enforcement` to `disabled` via the API, push,
flip it back. Fifteen seconds, fully auditable, and psychologically different from an invisible
bypass.

---

## What already exists — do not assume a clean slate

`.github/workflows/` is **not** empty. Two workflows were added before this spec was written and
are live:

| Workflow | Trigger | What it does |
|---|---|---|
| `claude-code-review.yml` | `pull_request` (opened, synchronize, ready_for_review, reopened) | Automated Claude review pass on every PR |
| `claude.yml` | issue/PR comments, reviews, issues opened | On-demand Claude, invoked by mentioning it in a comment |

Both authenticate with a `CLAUDE_CODE_OAUTH_TOKEN` repository secret, which is already
configured — verified by successful runs on three separate pull requests.

Two consequences:

**1. `ci.yml` is added alongside these, not into an empty directory.** Nothing here replaces
them.

**2. This is the "second reviewer" the solo-developer problem said was impossible — partly.**
Requirement 3 notes that GitHub will not let a PR author approve their own PR, so a real approval
gate is unattainable alone. `claude-code-review` does not solve that (an Action cannot satisfy a
required *human* approval), but it does supply the thing that approval was a proxy for: a second
pass over the diff that the author did not write. Combined with the PR-template checklist, that
covers most of what the Amazon CR ritual actually delivers.

**Do not make `Claude Code Review` a required status check.** It reports success regardless of
what it finds — it comments rather than failing — so requiring it would add a merge gate that
never blocks anything, while adding its runtime to every merge (10 minutes on a large diff, in
the observed runs). Worse, if the OAuth token ever expires, every PR in the repo becomes
unmergeable for a reason unrelated to code quality. Keep it advisory and let a human read its
comments.

The required checks stay `lint` and `test` — deterministic, fast, and meaningful when they
fail. GitHub may display these as `ci / lint` and `ci / test`, but the workflow name is not part
of a ruleset status-check context. [GitHub's ruleset troubleshooting documentation](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/troubleshooting-rules)
specifies the workflow format as the job name only.

## Components / changes

### `.github/workflows/ci.yml`

Runs on `pull_request` and `push` to `main`. Two jobs so failures are legible as separate status
checks: `lint` and `test`.

Pinned action versions, verified current as of August 2026:

| Action | Version | Note |
|---|---|---|
| `actions/checkout` | `@v7` | use `fetch-depth: 0` on the test job — `diff-cover` needs history |
| `actions/setup-python` | `@v7` | has built-in pip caching: `cache: 'pip'`. No separate cache step needed. |
| `actions/upload-artifact` | `@v7` | |
| `actions/download-artifact` | `@v8` | **off-by-one against upload — this is not a typo** |

Also set at workflow level: `permissions: contents: read` (least privilege) and a `concurrency`
group cancelling superseded PR runs.

Runner choice: `ubuntu-latest` for lint and unit tests (fast, and the offline core is pure
Python). `macos-26` for the integration job, since the production target is macOS. Free and
unlimited on public repos, so there's no cost reason to avoid macOS here.

### `.github/pull_request_template.md`

The Amazon-style CR checklist. This is the highest value-per-minute item in the whole spec and
has zero platform dependency. Sections: what changed and why; how it was tested; coverage;
backward compatibility; **rollback plan**; runtime verification evidence.

The rollback section is the one people leave out and the one Amazon's documented checklists
specifically call for.

### `.github/CODEOWNERS`

Assigns ownership. Works on public + Free. Note that `require_code_owner_review` must stay
`false` while there's one developer, for the self-approval reason above — the file is here so
ownership is documented and so the switch is one field when a collaborator arrives.

### `.github/rulesets/main-protection.json`

The ruleset, committed so it is reviewable rather than living only in the web UI. Applied with
`gh api`, **not** `gh ruleset` — `gh ruleset` has only `check`, `list`, and `view` subcommands;
there is no `gh ruleset create`. Any guide showing one is wrong.

```json
{
  "name": "main-protection",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [],
  "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    { "type": "required_linear_history" },
    { "type": "pull_request", "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": true,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": true,
        "allowed_merge_methods": ["squash"] } },
    { "type": "required_status_checks", "parameters": {
        "strict_required_status_checks_policy": true,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          { "context": "lint" },
          { "context": "test" } ] } }
  ]
}
```

Ship it with `"enforcement": "active"`. A non-blocking evaluation period would be preferable,
but GitHub rejects `evaluate` on this repository's plan. Before creation, observe `lint`, `test`,
and `integration` passing on the bootstrap pull requests and confirm the required contexts are
the job names `lint` and `test`. Task group 8 still performs the destructive and failing-check
tests; activation itself is moved here so application code is protected from its first pull
request.

### Coverage configuration in `pyproject.toml`

coverage.py has **no native per-file thresholds** — `fail_under` is documented as applying to
the *total* only. The dependency-free way to get per-file gates is to run `coverage report`
again with `--include` and an explicit `--fail-under`.

> ⚠️ **The gotcha that catches everyone:** if `fail_under = 85` sits in `pyproject.toml` and you
> run `coverage report --include=...` *without* `--fail-under`, coverage applies the 85 to the
> filtered subset rather than the total, producing confusing results. Always pass `--fail-under`
> explicitly on the per-file invocations.

Patch coverage uses **`diff-cover`** (10.4.2, actively maintained, released August 2026) against
`origin/main`. No account, no network service. Requires `fetch-depth: 0` on checkout or it
produces garbage.

Rejected alternative: GitHub's new native `Restrict code coverage` ruleset rule would be the
ideal solution, but it is part of GitHub Code Quality, gated to **Team or Enterprise Cloud** —
unavailable on Free or Pro, public or not. Worth rechecking in ~6 months.

### `Dockerfile` and dependency groups

`python:3.11-slim`, multi-stage: a builder stage that installs and builds, a test stage that
runs the suite. Used for reproducible builds and as the CI test environment.

Dependency groups in `pyproject.toml` make the hardware boundary explicit in the packaging
rather than in a comment:

| Group | Contents | In Docker? |
|---|---|---|
| core | `pydantic` | ✅ |
| perception | `opencv-contrib-python`, `mediapipe`, `silero-vad`, `onnxruntime`, `av` | ✅ (file-based only) |
| llm | `ollama`, `anthropic[bedrock]` | ✅ |
| dev | `pytest`, `pytest-cov`, `coverage`, `ruff`, `diff-cover` | ✅ |
| **capture** | `av` + a **native ffmpeg built `--enable-decklink`** | ❌ **host only** |

**Why `capture` can never be containerized on macOS**, stated plainly so nobody wastes a day on
it: the DeckLink Duo 2 is a PCIe card; Blackmagic Desktop Video is a macOS system extension;
Docker Desktop on macOS runs containers in a Linux VM with no PCIe passthrough. Docker Desktop
4.35+ added *USB* passthrough on macOS, which does not help — this is PCIe. Separately, CoreML
and the Apple Neural Engine are unreachable from a Linux container, so the perception tier's
performance story is also host-native. On a Linux host with a DeckLink, `--device` passthrough
would work; that is not the deployment target.

The honest summary for the README: **Docker makes the logic run anywhere. It cannot make the
hardware edge run anywhere, because the hardware edge is the part that is machine-specific by
definition.**

### `.github/workflows/promote.yml`

Build once, promote the same artifact. Stages as `needs:`-chained jobs, each declaring an
`environment:`.

| Env | Runner | Bake before promotion | Reviewer | App mode | Amazon analogue |
|---|---|---|---|---|---|
| `alpha` | ubuntu-latest | — | — | — | smoke tests, narrow scope |
| `beta` | macos-26 | — | — | — | full integration on production OS |
| `gamma` | self-hosted (the Mac) | **one full service** | you | `shadow` | production-like; validates deployability |
| `prod-onebox` | self-hosted | **one full service** | you | `assist` | one-box + bake |
| `prod` | self-hosted | — | you | `auto` | fleet |

Bake is measured in **services, not minutes** (R9). Amazon waits on the clock because their
services take continuous traffic; this one takes traffic once a week, so an hour of Tuesday
idle proves nothing. Amazon's own bake conditions include a data-volume clause ("wait for at
least 100 requests") for precisely this reason — the local translation of that clause is "wait
for one service." Environment wait timers may be set as a floor, but the promotion trigger is a
human approving after a clean service.

`concurrency` with `cancel-in-progress: false` — never cancel a deployment mid-flight.

`gamma` onward require a self-hosted runner on the Mac with the ATEM and DeckLink attached, so
they are **blocked until hardware is reachable**. `alpha` and `beta` work today.

### `scripts/watch_health.sh` and `scripts/rollback.sh`

The pieces GitHub cannot provide. Amazon's model is that the same alarm which pages the on-call
also triggers rollback, automatically, during bake — *"often, the rollback is already in
progress by the time the on-call engineer has been paged."*

There is no monitoring system here, so the alarm is defined from the application's own decision
log. The primary signal is **operator takeover rate**: if the human is overriding the AI
repeatedly, the AI is making bad calls. That is this domain's equivalent of a rising error rate,
and it is a better signal than anything generic. Secondary signals: heartbeat staleness (the app
stopped ticking) and sustained feed-unhealthy.

---

## Do NOT touch

- `atem_ai_vision_mixer/` and `tests/` — this spec adds CI *around* the code, it does not change
  the code.
- `docs/specs/offline-switching-core/` — a separate, already-approved contract.
- **Repository visibility.** Do not make the repo private; see the plan matrix above.
- `LICENSE`.

---

## Correctness properties

1. A direct push to `main` is rejected for every actor, including the repository owner.
2. A pull request with a failing check cannot be merged.
3. Every stage in the promotion pipeline consumes the artifact built by the `build` job — no
   stage rebuilds from source.
4. A failing stage prevents all later stages from running.
5. The `gamma` stage fails if the application performs any switcher write while in `shadow` mode.
6. The Docker image builds and passes tests with no network access to anything but the package
   index, and without any Blackmagic driver present.

---

## Verification strategy

- **CI self-test**: open a deliberately failing pull request (a lint error, then a failing test,
  then a coverage drop) and confirm each is blocked. A CI pipeline nobody has watched fail is
  not known to work.
- **Ruleset**: confirm the API reports `active`, then attempt `git push origin main` and confirm
  rejection. Open a pull request with a deliberate lint error, confirm it is blocked, fix it,
  and confirm it becomes mergeable.
- **Docker**: `docker build` and run the suite on a machine that has never had Blackmagic
  drivers installed. It must pass.
- **Pipeline**: trigger `promote.yml` manually and confirm `alpha` and `beta` pass, and that
  `gamma` correctly blocks pending a self-hosted runner.
- **Rollback**: deliberately trip the takeover-rate alarm in a scenario replay and confirm
  `rollback.sh` restores the prior version in `shadow` mode.

---

## Where this stops being Amazon-shaped

Stated honestly, ranked by how much it matters:

1. **Automatic rollback on production health.** GitHub is a CI/CD system, not an observability
   system. Hand-built here, and it is the piece that most determines whether this is genuinely
   Amazon-shaped or merely Amazon-flavoured.
2. **Deployment blockers driven by alarm state.** Same root cause — no alarm system to block on.
3. **Bake time gated on request volume, not just the clock.** Amazon's bake conditions include
   things like "wait for at least 100 requests to the Create API", because a quiet hour is not a
   validated hour. GitHub's wait timer is a dumb sleep. The closest local equivalent would be
   gating on decisions-made rather than minutes-elapsed.
4. **Merge queue** — unavailable on user-owned repos at any plan.
5. **A real second reviewer** — not a GitHub limitation.

One correction worth recording, since it is a common misconception: **"Bar Raiser" is an Amazon
hiring role, not a code review role.** It's an experienced interviewer with veto power on a
hiring loop. The phrase gets borrowed colloquially for code review, but Amazon publishes no
formal bar-raiser review program. The transferable mechanism — the thing that *is* documented in
the Builders' Library — is the **explicit written review checklist**, which is why
`pull_request_template.md` is the highest-value item in this spec.

---

## Resolved decisions

1. **Self-hosted runner security — RESOLVED: scope it narrowly.** A self-hosted runner on a
   public repo can execute code from any workflow run against that repo, and GitHub explicitly
   warns against the combination. Mitigation, to be implemented before the runner is registered:
   enable the repository setting requiring **manual approval for workflow runs from outside
   collaborators**, and restrict the runner so only `promote.yml` can use it — CI never touches
   it. Recorded as R9a.
2. **Bake and promotion — RESOLVED: service-aligned and manual.** Bake is one complete service,
   not a wall-clock timer, and every promotion from `gamma` onward requires a human approving
   after a clean service. Recorded as R9. Wait timers may be a floor, never the trigger.
3. **Ruleset dry run — RESOLVED: activate after bootstrap checks.** The public Free repository
   supports active rulesets, but the live API rejects `evaluate` enforcement as Enterprise-only.
   Create the ruleset as active only after the CI workflow has passed on the CI and review-process
   pull requests. Keep the negative push and failing-check tests in task group 8.

## Open questions

1. **Where does `prod` deploy to?** Currently assumed to be the same Mac, with promotion meaning
   a mode change rather than a file copy. If a spare Mac exists, `prod-onebox` and `prod` become
   genuinely different machines and the one-box analogy tightens considerably. Not blocking.
2. **What counts as "a clean service"?** R11 defines the alarm conditions (takeover rate,
   heartbeat staleness, sustained feed-unhealthy), but the specific takeover-rate threshold that
   should block promotion needs a number, and that number can only come from watching a few real
   services in `shadow` mode. Expect to set it during hardware bring-up, not before.
