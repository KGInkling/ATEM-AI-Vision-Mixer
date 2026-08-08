# Tasks: CI/CD Pipeline and Review Process

Work top to bottom. Do not start a group until the checkpoint before it passes.

> **Delivery rhythm:** one pull request per implementation task group — **not** one large PR at
> the end. Checkpoint-only groups are gates you run before opening a PR, not PRs themselves.
> **Hard stop when this spec's in-scope groups are complete**: report and wait. Full rule in
> [`../HANDOFF.md`](../HANDOFF.md).

**Read `requirements.md` and `design.md` first.** Requirement numbers (R1–R13) are referenced
throughout.

## Ordering — this spec is split, and part of it goes FIRST

**Do not do this spec in one pass, and do not do it after spec 1.** The review gate has to exist
before the first application code goes through it.

| Groups | When | Why |
|---|---|---|
| **1, 2, 4, 6** | **Before any application code** | CI workflow, PR template, CODEOWNERS, ruleset in `evaluate` mode. None of it needs application code. Putting it up first means every pull request in the project is gated — including spec 1's safety controller, which is the single PR you least want ungated. |
| **3, 5, 7, 8** | After spec 1's task groups 1–3 | Coverage gates need code to measure — this is the only genuine dependency. Docker and flipping the ruleset to `active` go here too. |
| **9–11** | **Blocked (~Sept 2026)** | Need a self-hosted runner on the Mac with the ATEM and DeckLink attached. |

See `docs/specs/README.md` for the full cross-spec implementation order.

---

## Task Group 1: Verify the starting state

- Confirm the repo is still **public**: `gh repo view --json visibility`. If it has gone
  private, **stop** — most of this spec silently stops working (R13, and the plan matrix in
  `design.md`).
- Confirm no rulesets exist yet: `gh api /repos/KGInkling/ATEM-AI-Vision-Mixer/rulesets`.
- Confirm `gh auth status` shows the `repo` scope.

## Task Group 2: CI workflow (R1)

- Create `.github/workflows/ci.yml`, triggered on `pull_request` and `push` to `main`.
- Two jobs, so they surface as separate status checks: **`lint`** and **`test`**.
- Set `permissions: contents: read` at workflow level, and a `concurrency` group that cancels
  superseded PR runs.
- Pin: `actions/checkout@v7`, `actions/setup-python@v7`. Use `setup-python`'s built-in
  `cache: 'pip'` — do **not** add a separate `actions/cache` step.
- Set `fetch-depth: 0` on the `test` job's checkout. `diff-cover` needs history; without it the
  patch-coverage numbers are garbage.
- `lint`: `ruff check .` and `ruff format --check .`.
- `test`: `pytest` with coverage on `ubuntu-latest`.
- Add a third job `integration` on `macos-26` running the scenario replays, since macOS is the
  production target. Free and unlimited on public repos.

## Task Group 3: Coverage gates (R5)

- Add `[tool.coverage.run]` and `[tool.coverage.report]` to `pyproject.toml`: `branch = true`,
  `fail_under = 85`, `show_missing = true`, and an `exclude_also` list covering
  `if TYPE_CHECKING:`, `raise NotImplementedError`, and `if __name__ == .__main__.:`.
- Add per-file gates as a CI step reading a `.coverage-thresholds` file
  (`atem_ai_vision_mixer/execution/controller.py:95`), looping over it with
  `coverage report --include="$path" --fail-under="$threshold"`.
- ⚠️ **Pass `--fail-under` explicitly on every per-file invocation.** If you rely on the
  `pyproject.toml` value, coverage applies it to the *filtered subset* rather than the total and
  the results are misleading. This is the single most common mistake with this approach.
- Add `diff-cover coverage.xml --compare-branch=origin/main --fail-under=90`, writing its
  markdown report into `$GITHUB_STEP_SUMMARY` so the number is visible on the run page.
- Do **not** reach for GitHub's native coverage ruleset rule — it is part of GitHub Code Quality,
  gated to Team/Enterprise Cloud, unavailable here.

## Task Group 4: Review process (R3, R4)

- `.github/pull_request_template.md` with the Amazon-style checklist: what changed and why, how
  it was tested, coverage, backward compatibility, **rollback plan**, runtime verification
  evidence. The rollback section is the one Amazon's documented checklists specifically call for
  and the one most templates omit.
- `.github/CODEOWNERS` assigning ownership to `@KGInkling`.
- Do **not** enable `require_code_owner_review` — a solo owner cannot approve their own pull
  request, so it would deadlock every one (R3).

## Task Group 5: Checkpoint

- Open a throwaway pull request. Confirm `lint`, `test`, and `integration` all appear as
  separate status checks and all pass.
- Note the **exact** check names as GitHub reports them (e.g. `ci / lint`) — the ruleset in the
  next group must match them character for character or the requirement silently never applies.

## Task Group 6: Repository settings and ruleset in evaluate mode (R2, R2a, R12)

**Branch cleanup (R2a)** — a repo setting, not a ruleset rule, so it is configured separately:

```bash
gh api --method PATCH /repos/KGInkling/ATEM-AI-Vision-Mixer -F delete_branch_on_merge=true
```

This deletes the remote head branch on merge. It does **not** touch local clones — deleting the
local branch after a merge stays the implementer's job.

**The ruleset:**

- Commit `.github/rulesets/main-protection.json` (full JSON in `design.md`), with
  `"enforcement": "evaluate"` and `"bypass_actors": []`.
- Apply it: `gh api --method POST /repos/KGInkling/ATEM-AI-Vision-Mixer/rulesets --input
  .github/rulesets/main-protection.json`.
- ⚠️ Use `gh api`. **`gh ruleset` has no `create` subcommand** — only `check`, `list`, `view`.
- Record the returned ruleset ID in the JSON file as a comment or in the README.
- Leave it in evaluate mode for about a week and read what it *would* have blocked.

## Task Group 7: Docker (R6, R7)

- `Dockerfile`: `python:3.11-slim`, multi-stage (builder + test).
- Restructure `pyproject.toml` optional-dependency groups per the table in `design.md`: `core`,
  `perception`, `llm`, `dev`, and **`capture`**.
- The image installs everything **except `capture`**.
- `docs/DOCKER.md` explaining the hardware boundary in plain language: the offline core,
  directors, file-based perception, and tests are containerizable; **live DeckLink capture and
  CoreML/ANE inference are host-native only**, because the Duo 2 is a PCIe card, Blackmagic
  Desktop Video is a macOS system extension, and Docker Desktop on macOS has no PCIe passthrough.
  Note that Docker Desktop 4.35+ added *USB* passthrough, which does not apply here.
- Verify on a machine with no Blackmagic drivers: `docker build` then run the suite. It must pass.

## Task Group 8: Activate and verify enforcement (R2)

- Flip the ruleset to active:
  `gh api --method PUT /repos/.../rulesets/<ID> -f enforcement=active`.
- Attempt `git push origin main` and **confirm it is rejected**. If it succeeds, the ruleset is
  misconfigured — most likely `bypass_actors` is not empty.
- Open a pull request with a deliberate lint error, confirm merge is blocked, fix, confirm merge
  unblocks.
- Document the emergency escape hatch in the README: flip `enforcement=disabled`, push, flip
  back. Deliberate and auditable, not an invisible bypass.

> **Ship here.** Everything below needs hardware.

---

## Task Group 9 (BLOCKED — needs hardware): Self-hosted runner (R9a)

**Order matters: do the mitigation before registering the runner, not after.** A self-hosted
runner on a public repo can otherwise execute code from any workflow run against that repo.

- In repo Settings → Actions, require **manual approval for workflow runs from all outside
  collaborators**.
- Register a self-hosted runner on the Mac, labelled `[self-hosted, macOS, ARM64, atem-lab]`.
- Restrict the runner so only `promote.yml` targets it. `ci.yml` must never use it.
- Verify with a pull request from a throwaway account: the workflow must sit pending approval
  rather than executing on your Mac.

## Task Group 10 (BLOCKED): Promotion pipeline (R8, R9, R10)

- Create the five Environments (`alpha`, `beta`, `gamma`, `prod-onebox`, `prod`) per the table in
  `design.md`. `gamma`, `prod-onebox`, and `prod` each take **you as a required reviewer** —
  promotion is manual after one clean service (R9). Wait timers are optional, and only ever a
  floor, never the trigger.
- `.github/workflows/promote.yml`: a `build` job producing one artifact, then stage jobs chained
  with `needs:`, each declaring its `environment:`.
- Every stage **downloads the same artifact** — no stage rebuilds from source (R8).
- `actions/upload-artifact@v7` / `actions/download-artifact@v8` — the version numbers genuinely
  differ; this is not a typo.
- `concurrency` with `cancel-in-progress: false`. Never cancel a deployment mid-flight.
- `gamma` runs the app in `shadow`, `prod-onebox` in `assist`, `prod` in `auto` (R10).
- `gamma` must **fail** if the app performs any switcher write, since shadow mode must not write.

## Task Group 11 (BLOCKED): Health and rollback (R11)

- `scripts/watch_health.sh`: read the app's decision log; alarm on **operator takeover rate above
  threshold** (primary), heartbeat staleness, and sustained feed-unhealthy.
- `scripts/rollback.sh`: restore the previously deployed version and restart it in `shadow` mode.
- Wire `watch_health.sh` into the bake period so an alarm triggers rollback automatically, per
  Amazon's model where the alarm that pages the on-call is the same one that rolls back.
- Test it: deliberately trip the takeover alarm in a scenario replay and confirm rollback fires.

---

## Task Group 12: Tests and evidence

- Confirm all six correctness properties from `design.md` hold.
- Capture evidence: a screenshot or log of the rejected `git push origin main`, a blocked pull
  request, and a successful Docker test run on a driver-free machine.

## Task Group 13: Submit for review

- Branch first — the point of this spec is that `main` is not directly pushable.
- Commit with a clear message: summary, then problem / fix / testing.
- Open a draft pull request using the new template, exercising it for real.
- **Clean up**: no scratch files left in the working tree. `git status` should show only intended
  deliverables (R13).
