# Design: Offline Switching Core

## Approach

Build the decision-making half of the switcher first, with the hardware replaced by a fake.
The reason is practical: the ATEM and DeckLink are ~1 month out of reach, and the safety
controller — the module that decides *when* to cut — is both the most important part of the
project and the part that needs no hardware at all. Everything here runs on a laptop with
`pytest` and nothing else.

Four deliberate constraints keep the code readable for someone new to Python:

1. **Two dependencies only.** `pydantic` at runtime, `pytest` for tests. No OpenCV, no ML, no
   network, no async. Those arrive in later specs, behind interfaces this spec defines.
2. **Everything synchronous.** One `tick()` call per loop iteration. Async concurrency is a real
   need for the LLM tiers later, but it would obscure the logic here.
3. **The controller is a straight run of guard clauses.** Each safety rule is one `if` that
   returns a `Decision` with a reason string. The function reads top-to-bottom in the same order
   as the rules in `requirements.md`. No strategy objects, no rule registry, no plugin system —
   a reader should be able to follow the whole decision path in one pass.
4. **No ambient clock.** `tick()` takes `now` as an argument. That single choice is what makes
   the entire safety layer testable: a test can simulate ten minutes of a sermon in
   microseconds by passing increasing floats.

### Why Pydantic for the contracts

`WorldState` and `ShotIntent` could be plain dicts. Pydantic models are chosen because they buy
three things for one import:

- **Validation at the boundary.** A `framing_score` of `1.7` raises immediately, at the line
  that produced it, instead of silently skewing a comparison ten modules later.
- **A JSON Schema for free.** `ShotIntent.model_json_schema()` is exactly what the local LLM
  needs to constrain its output to guaranteed-parseable JSON in a later spec. Writing the model
  once serves both the type checker and the LLM.
- **Editor autocomplete.** `state.audio.pause_ms` autocompletes and typo-checks;
  `state["audio"]["pause_ms"]` does neither.

### Why `typing.Protocol` for the switcher

`SwitcherClient` is declared as a `Protocol` — a list of method signatures any switcher must
have. Unlike a base class, nothing has to inherit from it: `FakeSwitcher` and, later,
`PyatemSwitcher` just define the methods and are automatically compatible. That keeps the fake
free of inheritance machinery and makes the eventual swap to real hardware a one-line change in
`app.py`.

---

## Components / changes

All paths relative to the repo root. Every file below is new.

- `pyproject.toml`: hatchling build, package `atem_ai_vision_mixer`, requires-python `>=3.11`,
  dependency `pydantic>=2`, optional-dependency group `dev = ["pytest"]`. Ruff config moved here
  from `.vscode/` so it applies outside the editor too.

- `atem_ai_vision_mixer/world_state.py`: `ProgramState`, `AudioState`, `CameraState`,
  `WorldState` — Pydantic models per Requirement 1. Score fields use
  `Annotated[float, Field(ge=0.0, le=1.0)]` so the bounds are declared once, on the type.

- `atem_ai_vision_mixer/director/intent.py`: `ShotIntent` model, a `Reason` string-enum of
  allowed reason codes, and `shot_intent_schema(camera_ids)` which returns the JSON Schema with
  an `enum` patched into the `camera` property. That patch is what stops a future LLM from
  inventing a camera that isn't connected.

- `atem_ai_vision_mixer/director/rules.py`: `RulesDirector.decide(state) -> ShotIntent`. Scores
  each healthy camera as a weighted sum of `framing_score`, a subject-present bonus, and a
  penalty for the live camera going stale, then names the winner. Pure function of its input.

- `atem_ai_vision_mixer/execution/switcher.py`: the `SwitcherClient` Protocol —
  `program_input()`, `preview_input()`, `in_transition()`, `set_preview(camera)`, `cut()`,
  `auto()`.

- `atem_ai_vision_mixer/execution/fake_switcher.py`: `FakeSwitcher`, an in-memory implementation.
  Holds `_program`, `_preview`, and a transition end-time. Its `cut()` swaps the two buses, which
  is the ATEM behaviour most likely to trip up controller logic if the fake got it wrong. Adds
  `simulate_operator_take(camera)` for takeover tests, and a `writes` list recording every
  state-changing call so tests can assert shadow mode wrote nothing.

- `atem_ai_vision_mixer/execution/controller.py`: `ControllerConfig` (a `@dataclass` holding
  every tunable from `requirements.md` with its default), `Decision`, `Mode`, and `Controller`.
  `Controller.tick(state, intent, now) -> Decision` runs the guard clauses in order and performs
  the permitted switcher writes.

- `atem_ai_vision_mixer/app.py`: wires director → controller → fake switcher and runs a scripted
  scenario, printing one JSON line per decision. Entry point for runtime verification.

- `atem_ai_vision_mixer/scenarios.py`: hand-written `WorldState` sequences that stand in for
  perception until the perception spec lands — a normal sermon, a camera that fails mid-service,
  a human takeover, and a speaker who never pauses.

- `tests/`: `test_world_state.py`, `test_intent.py`, `test_fake_switcher.py`,
  `test_rules_director.py`, `test_controller.py`, `test_integration.py`.

---

## Do NOT touch

- `LICENSE` — leave byte-identical.
- `.gitignore` — already correct for a Python project.
- `.venv/` — no new runtime dependencies beyond `pydantic`. In particular **do not** install or
  import `PyATEMMax`; the project has moved to `pyatem`, and neither belongs in this spec.
- Anything under `docs/specs/` other than this folder.
- No `capture/` or `perception/` package in this spec. Those are named in the plan and belong to
  the perception spec; creating empty stubs now would invite premature coupling.

---

## Correctness properties

These are invariants over *any* sequence of ticks, not single cases. They become the assertions
in `test_integration.py`, checked after every tick of a long scripted run.

1. **Shadow mode never writes.** For any tick sequence with `mode="shadow"`,
   `fake_switcher.writes` stays empty.
2. **Cuts respect cooldown.** Any two consecutive controller-commanded cuts are at least
   `cut_cooldown_seconds` apart.
3. **Cuts respect dwell.** No cut is commanded when `program.seconds_live < min_dwell_seconds`.
4. **Never cut to a broken feed.** No commanded cut ever targets a camera whose `feed_healthy`
   is `False` in the `WorldState` that produced the decision.
5. **Never cut over speech.** No cut is commanded while `audio.speaking` is `True`.
6. **Rate cap holds.** In any trailing 60-second window, commanded cuts never exceed
   `max_cuts_per_minute`.
7. **Determinism.** Two `Controller` instances with identical configs, fed identical
   `(state, intent, now)` sequences, produce identical `Decision` sequences.

Property 7 is the one that makes the rest checkable, and it is the reason `now` is a parameter.

---

## Verification strategy

- **Unit tests**, one file per module. Each requirement in `requirements.md` maps to at least one
  named test — e.g. `test_holds_when_below_min_dwell`, `test_never_cuts_to_unhealthy_camera`,
  `test_takeover_drops_to_shadow_for_configured_period`. Controller tests are table-driven where
  a rule has several boundary cases (the pause-window rule has four).
- **Integration test**: `test_integration.py` runs each scenario in `scenarios.py` end to end
  through `RulesDirector` → `Controller` → `FakeSwitcher`, asserting all seven correctness
  properties after every tick. This is where a rule interaction that no single unit test covers
  will surface.
- **Runtime verification**: `python -m atem_ai_vision_mixer.app --scenario sermon --mode shadow`
  prints a JSONL decision log. Capture that output as evidence. A reader should be able to look
  at the log and see the switcher holding through a breath pause and cutting on a sentence
  boundary, which is the whole point of the feature.
- **Coverage**: the repo sets no bar today. This spec proposes **95% on
  `execution/controller.py`** — it is the safety layer, and an untested branch there is a rule
  that can silently fail on air — and **85% across the rest of the new package**.

---

## Open questions

1. **`assist` mode staging behaviour.** Two readings: continuously mirror the AI's preferred
   camera onto the preview bus, or only stage when a cut would otherwise have fired. *Proposed:
   the second* — a preview bus that changes constantly is noise to a human operator, whereas one
   that changes only when the AI genuinely wants to cut is a usable suggestion. Confirm.
2. **Camera identity.** This spec assumes ATEM video source numbers are small positive integers
   and that the same integer identifies a camera in `WorldState.cameras`, in `ShotIntent.camera`,
   and on the switcher. That mapping needs confirming against the real ATEM during hardware
   bring-up, but it does not block anything here.
3. **All tuning defaults are placeholders.** `min_dwell_seconds=8.0`,
   `hysteresis_margin=0.15`, `max_cuts_per_minute=6` and the rest are informed starting points,
   not measured values. They are grouped in one `ControllerConfig` dataclass precisely so they
   can be tuned against real footage without touching logic.
4. **No `AGENTS.md` exists in this repo.** Conventions were inferred from `.vscode/settings.json`
   (Ruff, format-on-save, organise-imports). Worth adding one later so a fresh session does not
   have to infer.
