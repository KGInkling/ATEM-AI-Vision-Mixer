# Implementation handoff

Give this to the implementing agent (Codex) as its starting prompt. It is written to be
self-contained — an agent with none of the design conversation should be able to work from it.

---

## The prompt

> You are implementing the ATEM AI vision mixer, an AI camera switcher for church sermons that
> controls a Blackmagic ATEM Constellation 2 M/E 4K.
>
> **Start by reading `docs/specs/README.md`.** It is the source of truth for scope, architecture,
> and the verified hardware facts. Then read the spec set you are working on.
>
> **There are five spec sets in `docs/specs/`. Spec numbers are NOT the implementation order.**
> The review gate goes up before the first application code goes through it. Work in this order:
>
> 1. **`cicd-pipeline`, task groups 1, 2, 4, 6** — CI workflow, PR template, CODEOWNERS, and the
>    active ruleset. GitHub rejects non-blocking `evaluate` mode on this plan, so activation
>    follows two green bootstrap pull requests and protects the first application pull request.
> 2. **`offline-switching-core`, task groups 1–3** — scaffolding and the two contracts. This is
>    the first real pull request and the pipeline's first real exercise.
> 3. **`cicd-pipeline`, task groups 3, 5, 7, 8** — coverage gates (there is now code to measure),
>    Docker, and negative enforcement tests.
> 4. **`offline-switching-core`, remaining groups** — fake switcher, safety controller, rule-based
>    director, scenarios, tests.
> 5. **`perception`** — video and audio into a `WorldState`.
> 6. **`llm-directors`** — local Ollama tier and AWS Bedrock tier.
> 7. **`hardware-bringup`** — real drivers. **Blocked until hardware is available, ~Sept 2026.**
>
> The only reason spec 2 is split rather than done wholesale first: the coverage gate needs code
> to measure. Everything else in it is independent of the application.
>
> Each spec set has three files, and they are meant to be read in this order:
> `requirements.md` (what it must do, as testable SHALL statements), `design.md` (the decisions
> you should not make on your own, and why), `tasks.md` (an ordered checklist to work top to
> bottom).
>
> **Do not skip ahead.** Each spec's `tasks.md` has checkpoint gates. Do not start a task group
> until the checkpoint before it passes. Later specs depend on contracts defined in earlier ones.
>
> **Rules that apply to every spec:**
>
> - **Write beginner-readable code.** The repository owner is new to Python and learns by reading
>   this code. Plain, explicit constructs. Guard clauses over nested conditionals. Named
>   constants over inline magic numbers. A docstring on every public function saying what it does
>   and why. If a construct needs a paragraph to explain, use the simpler one.
> - **Respect the "Do NOT touch" list** in each `design.md`. It is a hard boundary.
> - **Clean up temporary files.** Scratch output, dumped frames, generated media, and logs do not
>   belong in the working tree. Before finishing, `git status` should show only intended
>   deliverables.
> - **Deliver in small increments — this one is not negotiable.** See "Delivery rhythm" below.
>   One pull request per implementation task group, tests included in the same PR, and a **hard
>   stop at the end of every spec** to wait for human review.
> - **Branch first.** `main` is protected after spec 2 lands, and should be treated as protected
>   before that. One coherent change per pull request.
> - **If a spec seems wrong, say so rather than working around it.** Several specs note that
>   needing to change an earlier module means an earlier spec was wrong. That is a real signal —
>   raise it instead of special-casing.
> - **Flag anything you cannot verify.** Do not assert that something works if you have not run
>   it.
>
> **Environment:** Python 3.11.9, macOS, existing venv at `.venv/`. The repo is public on GitHub
> at `KGInkling/ATEM-AI-Vision-Mixer`, default branch `main`.
>
> Start with spec 1, task group 1.

---

## Delivery rhythm — small PRs, hard stops

**This governs every spec and overrides the "Submit for review" group at the end of each
`tasks.md`.** Those groups describe how a spec *closes*, not the only time a pull request opens.

### One pull request per implementation task group

Work one task group, open a pull request, get it merged, then start the next. Do not accumulate
three or four groups and land them together.

- **Checkpoint-only groups are not pull requests.** They are gates you run *before* opening the
  PR for the work they gate. A checkpoint that fails means the PR isn't ready.
- **Tests ship in the same pull request as the code they test.** Never in a later one. Where a
  spec has a trailing "Tests" group, treat it as a final sweep for gaps and cross-cutting
  property tests — not as the place testing begins.
- **Every pull request must independently pass** lint, tests, and the coverage gates. Never merge
  a red branch on the promise that the next one fixes it.
- Each PR uses the template from spec 2, with the runtime-verification evidence that spec asks
  for.

Why: a 2,000-line pull request gets rubber-stamped, which defeats the purpose of having a review
gate. Small, focused change requests are the norm this project is modelled on, and the review
discipline in spec 2 only pays off if the diffs are actually reviewable.

### Hard stop at the end of every spec

When a spec's task groups are all merged: **report what landed and stop.** Do not roll straight
into the next spec.

Report should cover: which task groups landed and their PR numbers, coverage numbers, anything
that deviated from the spec and why, any open question the spec raised that implementation
answered, and anything you could not verify.

Then wait to be told to continue. The specs are ordered because later ones build on contracts
earlier ones define — a wrong contract discovered two specs later is expensive, and these stops
are where that gets caught.

---

## Context an implementing agent should have

Short version of things that are easy to get wrong and are documented in the specs:

**Architecture.** Three layers. Perception produces a `WorldState`; directors consume it and
*suggest* a `ShotIntent`; a deterministic controller decides whether to act. **The controller is
the only thing that ever writes to the switcher**, and its vetoes apply identically to rule-based
and LLM-produced suggestions. That property is the entire safety argument for putting an AI near
a live production switcher — preserve it.

**Offline-first.** The hardware is real but unreachable until roughly September 2026. Everything
is built against a fake switcher and recorded video. This is not a workaround; it is why the
safety controller — the most important module — can be built and thoroughly tested first.

**Run modes are a safety ladder.** `shadow` (logs, writes nothing) → `assist` (stages on preview,
human takes it) → `auto`. Every shot is staged on preview and held for a review window before
promotion, so a human can always see what the AI intends and veto it. In spec 2 these same modes
become the deployment promotion stages.

**Library choices that were researched and settled** — do not substitute:

| Use | Not | Because |
|---|---|---|
| `pyatem` / OpenSwitcher | `PyATEMMax` | PyATEMMax is dormant since 2022, protocol-capped at ATEM firmware 7.5, has no per-field change events, and has an open unanswered issue for this exact switcher model |
| PyAV | `cv2.VideoCapture` | VideoCapture cannot decode audio at all, and audio is this project's most valuable signal |
| Pinned Silero ONNX model loaded directly with onnxruntime | `webrtcvad`, the `silero-vad` Python package, or silero via PyTorch | WebRTC VAD reports speech through an entire worship set; the Python package declares the large PyTorch stack even when only ONNX inference is wanted |
| `mediapipe.tasks.python.vision` | `mediapipe.solutions` | `mp.solutions` **no longer exists**. Every tutorial using it is dead code and fails on import |
| `opencv-contrib-python` (via mediapipe) | also installing `opencv-python` | They collide on the `cv2` namespace |

**Hardware facts that constrain the design:**

- The DeckLink Duo 2 is **4 × 3G-SDI, 1080p60 max — no 4K**. A 4K multiview is not capturable.
- It is a **PCIe card**, so any Apple Silicon Mac needs a Thunderbolt expansion chassis.
- Production target is an **M1 Pro with 16 GB**, which is why the local LLM caps at ~4B params
  and pose estimation is not run on every tile.
- Homebrew's stock ffmpeg **does not** include DeckLink support; the `homebrew-ffmpeg` tap does,
  and the SDK headers must be installed first.

**Two Bedrock details that silently break things:** model IDs carry an `anthropic.` prefix on
Bedrock, and Bedrock does **not** support automatic top-level `cache_control` — an explicit
breakpoint on the last system block is required or nothing caches.

---

## What is deliberately not specified

So nobody invents it:

- **Multi-site or multi-switcher support.** One switcher, one machine.
- **A web UI or remote control.** The application is a CLI. Observability is the decision log.
- **PTZ camera control.** The cameras are fixed; the AI selects shots, it does not frame them.
- **Transitions other than cut.** The controller commands cuts. Dissolves are noted as a possible
  future tunable in spec 5 and are not built.
- **Recording or streaming output.** The ATEM handles that; this project only decides shots.
