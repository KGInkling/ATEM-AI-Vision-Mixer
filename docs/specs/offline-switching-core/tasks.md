# Tasks: Offline Switching Core

Work top to bottom. Do not start a group until the checkpoint before it passes.

> **Delivery rhythm:** one pull request per implementation task group, with that group's tests in
> the same PR — **not** one large PR at the end. Checkpoint-only groups are gates you run before
> opening a PR, not PRs themselves. **Hard stop when this spec is complete**: report and wait.
> Full rule in [`../HANDOFF.md`](../HANDOFF.md).

**Read `requirements.md` and `design.md` first.** Requirement numbers (R1–R17) are referenced
throughout; every task traces back to one.

> ⚠️ **Do spec 2's task groups 1, 2, 4, and 6 before starting this spec.** That puts the CI
> workflow, PR template, CODEOWNERS, and the branch ruleset in place first, so every pull request
> here is gated — including the safety controller, which is the one that most needs review
> discipline. Then pause after **task group 3 below** and go do spec 2's groups 3, 5, 7, 8, which
> add the coverage gates now that there is code to measure. Full order in
> `docs/specs/README.md`.

**Audience note:** the person who owns this repo is new to Python. Favour plain, explicit code
over clever code. Guard clauses over nested conditionals, named intermediate variables over
long expressions, a docstring on every public function saying what it does and why. If a
construct needs a paragraph to explain, prefer the simpler one that doesn't.

---

## Task Group 1: Project scaffolding

- Create `pyproject.toml`: hatchling backend, package `atem_ai_vision_mixer`, `requires-python
  = ">=3.11"`, dependency `pydantic>=2`, optional group
  `dev = ["pytest", "pytest-cov", "ruff"]`.
- Put portable Ruff settings in `[tool.ruff]` in `pyproject.toml` so linting works outside the
  editor. Keep `.vscode/settings.json` byte-identical: its formatter and save actions are VS
  Code integration settings and cannot be moved into `pyproject.toml`.
- Create package directories with `__init__.py`: `atem_ai_vision_mixer/`,
  `atem_ai_vision_mixer/director/`, `atem_ai_vision_mixer/execution/`, and `tests/`.
- Add one scaffold test that imports `atem_ai_vision_mixer`, so this first application pull
  request exercises the test job instead of failing because pytest collected nothing.
- Do **not** create `capture/` or `perception/` — out of scope, see design "Do NOT touch".
- Install into the existing 3.11.9 venv: `.venv/bin/pip install -e ".[dev]"`.

## Task Group 2: Checkpoint

- `.venv/bin/python -c "import atem_ai_vision_mixer"` succeeds.
- `.venv/bin/pytest` runs the scaffold import test and passes.
- `.venv/bin/ruff check .` passes clean.

## Task Group 3: The two contracts (R1, R2)

- `world_state.py`: `ProgramState`, `AudioState`, `CameraState`, `WorldState` as Pydantic
  models. Bound every score field to `[0.0, 1.0]` with
  `Annotated[float, Field(ge=0.0, le=1.0)]` — declare the constraint on the type, once.
- `director/intent.py`: `Reason` (string enum of allowed reason codes), `ShotIntent`
  (`camera: int`, `confidence: float` bounded `[0, 1]`, `reason: Reason`).
- `director/intent.py`: `shot_intent_schema(camera_ids: list[int]) -> dict` — call
  `ShotIntent.model_json_schema()`, then patch `enum` into the `camera` property. Add a comment
  explaining that this is what will stop a future LLM naming a camera that isn't connected.
- Round-trip check per R1: `WorldState.model_validate_json(state.model_dump_json()) == state`.

## Task Group 4: The switcher interface and its fake (R3)

- `execution/switcher.py`: `SwitcherClient` as a `typing.Protocol` with `program_input()`,
  `preview_input()`, `in_transition()`, `set_preview(camera)`, `cut()`, `auto()`. Docstring it
  as "the set of methods any switcher must provide — real or fake".
- `execution/fake_switcher.py`: `FakeSwitcher`.
  - `cut()` **swaps** program and preview. This is real ATEM behaviour and the single most
    likely place for the fake to diverge — get it right or the controller will be written
    against a fiction.
  - `auto()` reports `in_transition() == True` until the configured duration elapses, then
    swaps. It takes `now` as a parameter for the same reason the controller does.
  - `simulate_operator_take(camera)` sets program directly, modelling a human hitting a button.
  - `writes: list[str]` records every state-changing call, so shadow-mode tests can assert it
    stayed empty.

## Task Group 5: Checkpoint

- `.venv/bin/pytest` green with the contract and fake-switcher tests written so far.
- `.venv/bin/ruff check .` clean.

## Task Group 6: Rules director (R14)

- `director/rules.py`: `RulesDirector.decide(state: WorldState) -> ShotIntent`.
- Score each **healthy** camera: weighted sum of `framing_score`, a bonus when
  `subject_present`, and a penalty that grows as the live camera's `seconds_live` increases
  (a shot going stale is a reason to look elsewhere).
- Keep the weights as named module-level constants with a comment each, not magic numbers
  inline.
- If no camera is healthy, return an intent naming the currently live camera with
  `reason=Reason.HOLD`. Never return `None` — R14.
- Must be a pure function: same `WorldState` in, same `ShotIntent` out, no state, no clock.

## Task Group 7: The safety controller (R4–R13, R15, R16)

This is the heart of the feature.

- `execution/controller.py`: `ControllerConfig` dataclass holding every tunable with its
  default from `requirements.md` (`min_dwell_seconds=8.0`, `cut_cooldown_seconds=2.0`,
  `cut_pause_min_ms=400`, `cut_pause_max_ms=1200`, `cut_delay_into_pause_ms=100`,
  `preview_review_seconds=2.0`, `hysteresis_margin=0.15`, `hysteresis_ticks=3`,
  `max_cuts_per_minute=6`, `takeover_shadow_seconds=30.0`, `stale_world_state_seconds=0.5`).
- `Mode` enum: `SHADOW`, `ASSIST`, `AUTO`. Default `SHADOW` (R12).
- `Decision` model: `action`, `camera`, `reason`, `effective_mode`, `timestamp` (R15).
- Controller state to track: `staged_camera`, `staged_at`, `last_commanded_program`,
  `cut_times`, `challenger_camera`, `challenger_ticks`, `takeover_until`.

`Controller.tick(state, intent, now) -> Decision` runs in **two phases**. That split *is* R13 —
keep it visible in the code structure, don't collapse it into one flat chain.

**Phase 1 — universal guards.** Apply to staging and promotion alike.

  1. stale world state → hold (R11)
  2. detect takeover; if inside the takeover shadow window → hold, no writes (R10)
  3. intent names an unknown camera → hold (R2)
  4. target unhealthy → discard any staged shot, hold (R7)

**Phase 2a — should we stage?** Deliberately **not** gated by the speech-pause rule: a shot may
be lined up while the speaker is still talking. Only the promotion waits for a pause.

  5. target already live and nothing staged → hold
  6. hysteresis margin not met, or not sustained long enough → hold (R8)
  7. otherwise `set_preview(target)`, record `staged_at` → action `stage` (mode-gated, R12)

**Phase 2b — should we promote what's staged?**

  8. preview bus no longer holds what we staged → human veto: discard, hold (R13)
  9. staged for less than `preview_review_seconds` → hold, reason `reviewing` (R13)
  10. below min dwell → hold (R4)
  11. inside cut cooldown → hold (R5)
  12. rate cap reached → hold (R9)
  13. speech/pause window disallows cutting → hold (R6)
  14. otherwise `cut()` → action `cut`. **`auto` mode only** — `assist` stages but never
      promotes, leaving the take to the human (R12, R13)

- **`tick()` must not read the clock.** Use the `now` parameter only. This is R16 and it is what
  makes every other rule testable — a test can simulate ten minutes in microseconds.
- Track cut timestamps in a plain list, pruning entries older than 60s, for the rate cap.
- Takeover detection: compare the switcher's reported program against what the controller last
  commanded, allowing for a settling window after its own `auto()` transitions.

## Task Group 8: Checkpoint

- `.venv/bin/pytest` green.
- `.venv/bin/ruff check .` clean.
- Confirm no import of `PyATEMMax`, `pyatem`, `cv2`, `mediapipe`, `torch`, `anthropic`, or
  `ollama` anywhere in the package (R17): `grep -rE "PyATEMMax|pyatem|cv2|mediapipe|torch|anthropic|ollama" atem_ai_vision_mixer/` returns nothing.

## Task Group 9: Scenarios and the runnable app

- `scenarios.py`: hand-written `WorldState` sequences standing in for perception —
  (a) `sermon`: a normal service with realistic speech/pause cycles;
  (b) `camera_fails`: a camera goes unhealthy while live;
  (c) `operator_takeover`: a human takes program mid-run;
  (d) `never_pauses`: a speaker who never pauses long enough to permit a cut.
- `app.py`: wire `RulesDirector` → `Controller` → `FakeSwitcher`, run a named scenario, print
  one JSON line per `Decision`. CLI: `--scenario`, `--mode`. Default `--mode shadow`.

## Task Group 10: Tests

- One test file per module. Every requirement R1–R17 maps to at least one **named** test — e.g.
  `test_holds_when_below_min_dwell`, `test_never_cuts_to_unhealthy_camera`,
  `test_takeover_drops_to_shadow_for_configured_period`, `test_shadow_mode_performs_no_writes`.
- Table-driven tests for the pause window (R6) — it has four boundary cases: speaking,
  too-short breath, in-window, and beyond the upper bound.
- `test_integration.py`: run each scenario end to end and assert **all seven correctness
  properties** from `design.md` after every tick. This is where rule interactions that no single
  unit test covers will surface.
- Include the determinism test (property 7): two controllers, identical config, identical input
  sequence, identical decision sequence.

## Task Group 11: Coverage checkpoint

- `.venv/bin/pytest --cov=atem_ai_vision_mixer --cov-report=term-missing`.
- **95% on `execution/controller.py`** — it is the safety layer; an untested branch there is a
  rule that can silently fail on air.
- **85% across the rest of the package.**
- Any uncovered line in `controller.py` must be justified in the PR description or covered.

## Task Group 12: Runtime verification

- Run `python -m atem_ai_vision_mixer.app --scenario sermon --mode shadow` and save the JSONL
  decision log.
- Read the log and confirm by eye that the behaviour is what the feature promises: the switcher
  **holds through short breath pauses and cuts on sentence boundaries**, holds while a shot is
  under minimum dwell, and never targets an unhealthy camera.
- Run `--scenario operator_takeover --mode auto` and confirm the log shows the controller
  dropping to shadow and making no further writes for the configured period.
- Attach both logs as PR evidence.

## Task Group 13: Submit for review

- The repo is on `main` with remote `github.com/KGInkling/ATEM-AI-Vision-Mixer`. **Branch
  first** — do not commit this directly to `main`.
- Commit with a clear message: one-line summary, then problem / fix / testing in a few lines
  each.
- Open a draft pull request: what changed, test results, coverage numbers, and the two decision
  logs from Task Group 12.
- Address automated-review comments; skip nitpicks. Cap at 3–5 rounds.
- Report any remaining comments that need human judgement.

---

## Out of scope — deliberately

These are named so nobody is tempted to start them here. Each gets its own spec.

- Real video/audio capture (`capture/`, ffmpeg + DeckLink) — hardware bring-up spec.
- Perception (`perception/`, OpenCV, MediaPipe, silero-vad) — perception spec.
- LLM directors (Ollama, AWS Bedrock) — director spec.
- The real `pyatem` switcher driver — hardware bring-up spec.
