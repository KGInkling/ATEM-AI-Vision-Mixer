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

## Review paths after legacy retirement

The repository has deterministic CI, CodeQL default setup for `actions` and `python`, GitHub's
Dependency Graph, and two retained Claude workflows:

| Workflow | Trigger | Role |
|---|---|---|
| `claude.yml` | issue and pull request mentions | On-demand assistance; keep unchanged |
| `claude-review.yml` | manual dispatch | Exact-SHA general-review evidence |

The retired automatic workflow omitted the plugin's `--comment` option and used a command that
skipped draft pull requests. Its successful check could therefore lack a visible review. It also
invoked Claude on repeated PR events rather than one deliberate request per frozen revision.

The manual workflow uses a stable repository prompt and schema-valid structured output rather
than the draft-skipping plugin command. Both paths coexisted during shadow verification. The
manual reviewer then produced a marked clean review on activation candidate PR #25 at head
`79a8935a62b83253f1b4cc58ce8e08c5b42dbe9e`, permitting removal of the automatic workflow.
The retirement revision still requires its own fresh CI, CodeQL, and manual-review evidence.

The live ruleset remains active, strict, bypass-free, and limited to `lint` and `test` until
activation. The committed candidate declares seven contexts, but no new context is required
on GitHub before the separately authorized activation change. GitHub rulesets use the job or
status context name, not the displayed workflow prefix.

## Components / changes

### `.github/workflows/claude-review.yml`

Manual dispatch takes a pull request number and expected 40-character head SHA. Four jobs split
trust and side effects:

| Job | Credential and permission boundary | Responsibility |
|---|---|---|
| `preflight` | pull requests read | reject malformed, non-default-ref, closed, non-draft, forked, wrong-base, stale, or duplicate input |
| `mark-pending` | statuses write; no model secret | publish `claude-review=pending` on the frozen SHA |
| `review` | contents/PR read plus Claude auth | trusted base at root, head under `pr-head/`, precomputed merge-base diff and source context, no model tools, structured output |
| `publish` | PR/status write; no model secret or checkout | recheck the head, publish and verify a marked review object, then set success or error |

The model never receives GitHub write permission and cannot run Bash, project code, tests, builds,
package managers, hooks, or repository configuration. Pull request artifacts remain untrusted
data. The exact base and head are separate full-history checkouts; reviewer instructions come from
the trusted base. A workflow-controlled step computes the merge base and diff before model use.
Claude runs with `--safe-mode` and `dontAsk`, with no filesystem, command, or MCP tools. Safe mode
disables repository customizations while preserving normal authentication. `--bare` cannot be used
with the subscription token: it ignores `CLAUDE_CODE_OAUTH_TOKEN` and would require an API key.
This distinction is documented in [Claude authentication](https://code.claude.com/docs/en/authentication)
and was reproduced with the pinned CLI 2.1.257 using a fixture token, without a model request.
The workflow supplies trusted instructions followed by an explicitly untrusted JSON diff and
source snapshot through
`--append-system-prompt-file`. A read allowlist alone would not isolate context: `dontAsk` still
allows ordinary workspace reads. The pinned action's argument parser also drops empty flag values,
so `--tools "Read"` limits the built-in set and `--disallowedTools "Read,mcp__*"` denies that remaining
tool and every MCP tool. No custom execution or permission helper is introduced.

The pull request title, body, comments, and prior review text are not forwarded to the model.
Binary changes, sensitive filenames/directories, non-allowlisted extensions, and symlink/submodule
Git modes on either side of the diff stop context preparation before the Claude secret is used.
Diffs disable rename detection so an old sensitive path cannot disappear behind a safe new name.
The runner serializes the full safe allowlisted tracked-text snapshot, omitting unchanged sensitive
or non-text paths while failing if such a path changed. It then scans the entire assembled context
for high-confidence credential markers, including unchanged source and trusted instructions.
This permits caller and contract tracing without model access to the raw checkout or Git config.

These controls follow the [Claude permission semantics](https://code.claude.com/docs/en/permissions)
and [CLI tool flags](https://code.claude.com/docs/en/cli-reference). The pinned action's
[`parse-sdk-options.ts`](https://github.com/anthropics/claude-code-action/blob/781d62e9d5fbd78b24dcdfd42858332d485fe462/base-action/src/parse-sdk-options.ts)
is the version-specific argument contract.

The publisher accepts only schema-valid output whose `base_sha` and `reviewed_sha` equal the frozen
scope. It requires `clean` with zero findings or `findings` with at least one consequential
finding. A GitHub review is posted with marker `<!-- manual-claude-review:v1 -->`, reviewer, base
SHA, commit ID/head SHA, verdict, summary, and findings. The workflow validates every schema field
and limit, then re-reads both the pull request and created review, requiring the complete published
body to match, before publishing `claude-review=success`. Model failure, malformed output, publication
failure, or base/head movement produces error or unavailable evidence, never clean.

Status success means the marked review evidence exists and is current. It is not a clean verdict
or approval: a `findings` review still requires independent verification and terminal disposition
before human-ready state.

Publication is idempotent for failed-job retries: the publisher reuses and re-verifies an existing
marked review for the same base/head pair before creating one, so a transient status failure cannot
duplicate the visible review or spend another model run.

During coexistence, the legacy workflow also emitted a CheckRun named `claude-review`. Historical
check rollups are therefore not proof of the manual path. Verification must read the commit
statuses endpoint, match the manual workflow run URL, and verify the marked review object on the
same SHA. The legacy workflow is retired before the manual status context is added to protection.

The action pins are `actions/checkout` v7.0.1 at
`3d3c42e5aac5ba805825da76410c181273ba90b1` and the official Claude Code Action v1 commit
`781d62e9d5fbd78b24dcdfd42858332d485fe462`. The workflow rejects `runner.debug` before model use
and forces `ACTIONS_STEP_DEBUG=false` at the action boundary because debug mode otherwise enables
full Claude output in public logs.

### Review instructions and templates

`AGENTS.md` is the concise repository guide for architecture, validation, privacy, delivery, and
the exact review sequence. `.github/review-prompts/claude.md` is the trusted consequential-defect
contract for the manual reviewer. The pull request template records cycle/profile, fingerprint,
scope and exclusions, reviewer walkthrough, terminal evidence, rollout, rollback, and separate
human approval. `.gitmessage` supplies parallel commit guidance and is enabled per clone with
`git config commit.template .gitmessage`; it adds no hook or enforcement path.

### `.github/workflows/ci.yml`

Runs on `pull_request` and `push` to `main`. Separate jobs keep each failure legible: `lint`,
`test`, `build`, `dependency-review`, and `integration`. Dependency review is pull-request-only
because it needs a base/head dependency delta. The live ruleset continues to require only `lint`
and `test` until the newer contexts are observed passing and activated separately.

Immutable action pins, verified from the official release tags in August 2026:

| Action | Release and commit | Note |
|---|---|---|
| `actions/checkout` | `v7.0.1` at `3d3c42e5aac5ba805825da76410c181273ba90b1` | use `fetch-depth: 0` on the test job — `diff-cover` needs history |
| `actions/setup-python` | `v7.0.0` at `5fda3b95a4ea91299a34e894583c3862153e4b97` | built-in pip caching; no separate cache action |
| `actions/dependency-review-action` | `v5.0.0` at `a1d282b36b6f3519aa1f3fc636f609c47dddb294` | Node 24 requires runner 2.327.1 or newer; the observed hosted runner is 2.336.0 |

Also set at workflow level: `permissions: contents: read` (least privilege) and a `concurrency`
group cancelling superseded PR runs. Checkout does not persist credentials after fetching.

Runner choice: `ubuntu-latest` for lint and unit tests (fast, and the offline core is pure
Python). `macos-26` for the integration job, since the production target is macOS. Free and
unlimited on public repos, so there's no cost reason to avoid macOS here.

The build job creates both distribution formats, installs the wheel without dependencies into a
clean virtual environment, changes out of the checkout, and imports a representative nested
capture module from inside that environment. This prevents either a source-tree import or an empty
top-level package from making an incomplete wheel look valid.

Dependency review uses GitHub's official action with `fail-on-severity: moderate` and
`fail-on-scopes: runtime`. It does not post pull-request comments, so `contents: read` is the only
job permission it needs.

The macOS job installs `.[perception,dev]`, imports PyAV and NumPy explicitly, and runs the full
suite with skip reasons. It proves the real recorded-media test is executable; it deliberately
does not import PyAV and OpenCV together as a preflight because their bundled native libraries
have a separately tracked same-process collision.

### Staged evidence activation

The evidence baseline, CodeQL setup, and manual review were delivered before enforcement.
The committed ruleset now declares the activation candidate: `lint`, `test`, `build`,
`integration`, `dependency-review`, `CodeQL`, and `claude-review`. Declaring this list does not
apply it to GitHub. Only the separately authorized live switch updates branch protection,
with the prior `lint`/`test` context list retained for rollback. A failed evidence-only job
blocks completion of its stage without implying that GitHub already enforces it.

Immediately before that live switch, every intended required context must be terminal and
successful together on the unchanged activation pull request head. Historical passes from other
pull requests or earlier revisions cannot be combined, and no context is activated piecemeal.

### `.github/pull_request_template.md`

The Amazon-style CR checklist. This is the highest value-per-minute item in the whole spec and
has zero platform dependency. It records workflow identity, risk, exact head SHA, affected
contracts, what changed and why, testing, coverage, compatibility, **rollback and rollout plans**,
runtime evidence, check/review states, and unavailable evidence.

The rollback section is the one people leave out and the one Amazon's documented checklists
specifically call for. Missing, stale, or unverifiable evidence is recorded as unavailable rather
than interpreted as clean.

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

The first threshold names `execution/controller.py`, which is intentionally scheduled after
this coverage task group. Until that file lands, the CI loop checks both the pull-request tree
and `origin/main`: missing from both means "not implemented yet" and produces a visible pending
notice. Missing only from the pull request means a protected file was deleted or renamed and
fails the build. This lets the 95% gate activate on the controller's first pull request without
either blocking earlier work or creating a deletion loophole.

Patch coverage uses the actively maintained **`diff-cover`** against `origin/main`. No account,
no network service. Requires `fetch-depth: 0` on checkout or it produces garbage. Its current
`--format markdown:<path>` option writes the report into the GitHub step summary; the older
`--markdown-report` option is deprecated in 10.5.0.

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
| perception | `opencv-contrib-python`, `mediapipe`, `onnxruntime`, `av`; pinned Silero ONNX model packaged separately | ✅ (file-based only) |
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
