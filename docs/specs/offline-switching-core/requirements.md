# Requirements: Offline Switching Core

The first of four specs for the ATEM AI vision mixer. This one delivers a complete, runnable
camera switcher that decides shots from a `WorldState` and drives a switcher — with every
safety rule enforced — using a fake in-process switcher and synthetic input. No camera, no
capture card, no ATEM, no ML dependencies.

Later specs cover: perception (real `WorldState` from video + audio), the LLM director tiers,
and hardware bring-up.

**Scope note on units.** All times in this spec are seconds as `float` unless a name ends in
`_ms`. `now` is always a caller-supplied monotonic timestamp, never read inside the module.

---

## Requirement 1: WorldState contract

The system SHALL define a `WorldState` model carrying everything a director needs to choose a
shot, and SHALL serialize it to JSON without custom encoders.

WHEN `WorldState.model_dump_json()` is called on a fully-populated instance
THEN the result SHALL be valid JSON, and `WorldState.model_validate_json()` of that string
SHALL produce an equal instance.

`WorldState` SHALL contain at minimum:
- `timestamp: float` — monotonic time the state was assembled
- `program: ProgramState` — `live_camera: int`, `seconds_live: float`
- `audio: AudioState` — `speaking: bool`, `pause_ms: int`, `level: float`
- `cameras: dict[int, CameraState]` keyed by ATEM video source number

`CameraState` SHALL contain at minimum:
- `feed_healthy: bool`
- `framing_score: float` in `[0.0, 1.0]`
- `subject_present: bool`
- `motion_score: float` in `[0.0, 1.0]`
- `defects: list[str]` — named framing defects, empty when none

WHEN any score field is given a value outside `[0.0, 1.0]`
THEN construction SHALL raise a validation error.

## Requirement 2: ShotIntent contract

The system SHALL define a `ShotIntent` model that a director emits to *suggest* a shot, and
SHALL expose a JSON Schema suitable for constraining LLM output.

`ShotIntent` SHALL contain exactly: `camera: int`, `confidence: float` in `[0.0, 1.0]`, and
`reason: str` drawn from a closed set of reason codes.

The closed reason-code set SHALL be:
- `hold` — keep the currently live camera
- `better_framing` — the suggested camera has stronger framing
- `subject_present` — the expected subject is present on the suggested camera
- `motion` — observed movement motivates the suggested camera
- `shot_variety` — the current shot needs a visual refresh

WHEN `shot_intent_schema(camera_ids=[1, 2, 3])` is called
THEN it SHALL return a JSON Schema dict whose `camera` property carries `enum: [1, 2, 3]`,
so an LLM cannot name a camera that does not exist.

WHEN a `ShotIntent` names a camera absent from the current `WorldState.cameras`
THEN the controller SHALL treat the intent as invalid and hold.

## Requirement 3: Fake switcher mirrors ATEM bus semantics

The system SHALL provide a `FakeSwitcher` implementing the `SwitcherClient` protocol entirely
in memory, faithful enough that controller logic written against it works unchanged against a
real ATEM.

WHEN `cut()` is called
THEN program and preview SHALL swap: the previous preview becomes program, and the previous
program becomes preview.

WHEN `auto()` is called
THEN `in_transition` SHALL report `True` for the configured transition duration, and the buses
SHALL swap only once the transition completes.

WHEN `simulate_operator_take(camera)` is called
THEN program SHALL change to that camera without the controller having commanded it, so
takeover detection can be tested.

## Requirement 4: Minimum dwell

The controller SHALL NOT cut away from a camera that has been live for less than
`min_dwell_seconds` (default `8.0`).

WHEN `world_state.program.seconds_live < min_dwell_seconds`
THEN the decision SHALL be `hold`, whatever the intent's confidence, and the reason SHALL be
`min_dwell`.

## Requirement 5: Cut cooldown

The controller SHALL enforce a hard lockout after any cut it commands.

WHEN a cut was commanded less than `cut_cooldown_seconds` ago (default `2.0`)
THEN the decision SHALL be `hold` with reason `cooldown`, even if minimum dwell has elapsed.

## Requirement 6: Cut only on a graded speech pause

The controller SHALL cut only during a pause long enough to be a sentence boundary, and SHALL
place the cut inside the pause rather than at its edge.

WHEN `audio.speaking` is `True`
THEN the decision SHALL NOT be `cut`.

WHEN `audio.pause_ms < cut_pause_min_ms` (default `400`)
THEN the decision SHALL NOT be `cut` — this is a breath, not a boundary.

WHEN `cut_pause_min_ms <= audio.pause_ms <= cut_pause_max_ms` (default `1200`)
AND `audio.pause_ms >= cut_delay_into_pause_ms` (default `100`)
AND every other rule permits it
THEN the decision SHALL be `cut`.

WHEN `audio.pause_ms > cut_pause_max_ms`
THEN cutting SHALL still be permitted — extended silence is a valid cut point.

## Requirement 7: Feed-health veto

The controller SHALL refuse to put an unhealthy camera on program, regardless of what any
director suggests.

WHEN the intent names a camera whose `feed_healthy` is `False`
THEN the decision SHALL be `hold` with reason `unhealthy_target`, and no switcher write
SHALL occur.

## Requirement 8: Hysteresis

The controller SHALL hold the current shot unless a challenger is clearly better, sustained
over time.

WHEN the intent names a camera other than the live one
AND that camera's `framing_score` does not exceed the live camera's by at least
`hysteresis_margin` (default `0.15`)
THEN the decision SHALL be `hold` with reason `insufficient_margin`.

WHEN the margin is met but has been met for fewer than `hysteresis_ticks` consecutive ticks
(default `3`)
THEN the decision SHALL be `hold` with reason `building_confidence`.

WHEN the intent changes to a different challenger camera
THEN the consecutive-tick counter SHALL reset to zero.

## Requirement 9: Cut rate cap

The controller SHALL NOT exceed `max_cuts_per_minute` (default `6`) over any trailing
60-second window.

WHEN the cap has been reached
THEN the decision SHALL be `hold` with reason `rate_capped`.

## Requirement 10: Human takeover detection

The controller SHALL detect that a human changed program and SHALL immediately stop competing
with them.

WHEN the switcher reports a program source differing from what the controller last commanded
AND the controller is not inside its post-command settling window
THEN the controller SHALL record a takeover, SHALL switch its effective mode to `shadow` for
`takeover_shadow_seconds` (default `30.0`), and SHALL perform no switcher writes for that
period.

WHEN `takeover_shadow_seconds` has elapsed with no further takeover
THEN the controller SHALL return to its configured mode.

## Requirement 11: Stale-data failsafe

The controller SHALL fail toward doing nothing when it cannot see.

WHEN `now - world_state.timestamp > stale_world_state_seconds` (default `0.5`)
THEN the decision SHALL be `hold` with reason `stale_world_state`, and no switcher write
SHALL occur.

## Requirement 12: Run modes gate the write path

The controller SHALL support three modes, and the mode SHALL determine which switcher calls
are permitted.

WHEN mode is `shadow`
THEN the controller SHALL compute and return a full decision but SHALL make **zero** calls to
any switcher method that changes state.

WHEN mode is `assist`
THEN the controller MAY call `set_preview()` but SHALL NOT call `cut()` or `auto()`.

WHEN mode is `auto`
THEN the controller MAY call `set_preview()`, `cut()`, and `auto()`.

WHEN no mode is supplied
THEN the mode SHALL default to `shadow`.

## Requirement 13: Preview-then-promote

The controller SHALL never cut straight to program. Every shot SHALL be staged on the preview
bus first, held for a review window, and only then promoted. This is how a human operator
works — line the shot up, then take it on a beat — and it gives a human time to see what the
AI intends and veto it.

WHEN the controller selects a new target camera and all *staging* rules permit
THEN it SHALL call `set_preview(target)` and record the time it staged.

WHEN a camera has been staged for less than `preview_review_seconds` (default `2.0`)
THEN the decision SHALL be `hold` with reason `reviewing`, and no cut SHALL occur.

WHEN the review window has elapsed AND all cut rules still permit
THEN the controller SHALL promote the staged camera to program.

**Staging and cutting are gated differently, and this separation is deliberate:**

WHEN `audio.speaking` is `True`
THEN staging SHALL still be permitted — a shot may be lined up during speech — but cutting
SHALL NOT be (Requirement 6). The speech-pause rule gates the *promotion*, not the staging.

WHEN the preview bus changes to a source the controller did not stage
THEN the controller SHALL treat that as a human veto, SHALL discard the staged shot, and SHALL
NOT promote it.

WHEN the staged camera stops satisfying any cut rule before promotion — it becomes unhealthy,
the director changes its mind, or a takeover is detected —
THEN the staged shot SHALL be discarded rather than promoted.

WHEN mode is `assist`
THEN the controller SHALL stage but SHALL NEVER promote, leaving the take to the human
operator.

## Requirement 14: Rule-based director

The system SHALL provide a director that produces a `ShotIntent` from a `WorldState` using
deterministic scoring only, with no model inference and no network access.

WHEN every camera is unhealthy or absent
THEN the director SHALL return an intent naming the currently live camera with reason `hold`,
never `None`.

WHEN the same `WorldState` is passed twice
THEN the same `ShotIntent` SHALL be returned — the director is a pure function.

## Requirement 15: Decisions are observable

The controller SHALL return a `Decision` record for every tick, suitable for logging to JSONL
and for later comparison against a human operator's real cut list.

`Decision` SHALL contain: `action` (`hold` | `stage` | `cut`), `camera: int | None`,
`reason: str`, `effective_mode: str`, and `timestamp: float`.

## Requirement 16: Determinism (SHALL NOT read the clock)

The controller SHALL NOT call `time.time()`, `time.monotonic()`, or any other ambient clock.

WHEN `Controller.tick(world_state, intent, now)` is called twice with identical arguments and
identical prior controller state
THEN it SHALL return identical decisions.

## Requirement 17: No regressions (SHALL NOT)

This change SHALL NOT introduce any dependency on `PyATEMMax`, `pyatem`, `opencv`,
`mediapipe`, `onnxruntime`, `torch`, `anthropic`, `ollama`, or any network call.

This change SHALL NOT create, modify, or delete any file outside `atem_ai_vision_mixer/`,
`tests/`, `docs/specs/`, `pyproject.toml`, and `README.md`.

WHEN the offline switching core is complete
THEN `pip install -e .` followed by `pytest` SHALL pass on a machine with **no** ATEM, no
capture card, no GPU, and no internet connection.
