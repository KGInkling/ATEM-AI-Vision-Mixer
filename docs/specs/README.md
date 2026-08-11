# Spec index and roadmap

The full build plan for the ATEM AI vision mixer, and the status of each spec that implements
part of it. **This file is the source of truth for scope.** Individual specs under
`docs/specs/<feature>/` are the implementation contracts.

## Status

All five specs are written.

| # | Spec | Covers | Depends on | Status |
|---|---|---|---|---|
| 1 | [`offline-switching-core`](offline-switching-core/) | Contracts, fake switcher, safety controller, rule-based director | — | Ready |
| 2 | [`cicd-pipeline`](cicd-pipeline/) | GitHub Actions, rulesets, PR review process, Docker, promotion stages | — for the gate; 1 for coverage | Ready — Groups 1–8 |
| 3 | [`perception`](perception/) | Capture interface, file source, tiles, feed health, motion, VAD, people, framing | 1 | Ready |
| 4 | [`llm-directors`](llm-directors/) | Local LLM (Ollama) + AWS Bedrock director tiers | 1, helped by 3 | Ready |
| 5 | [`hardware-bringup`](hardware-bringup/) | Real `pyatem` driver, DeckLink capture, bench-test checklist | 1, 3 | Blocked on hardware (~Sept 2026) |

## Implementation order — the pipeline goes up first

**Spec numbers are not the implementation order.** Spec 2 is split, because the review gate must
exist *before* the first application code goes through it. Otherwise the largest and most
safety-critical pull request in the project — the safety controller — is the one that never went
through the process, which is exactly backwards.

Work in this order:

| Step | Do | Why here |
|---|---|---|
| **1** | Spec 2, task groups **1, 2, 4, 6** | CI workflow, PR template, CODEOWNERS, and the active ruleset. **None of this needs application code.** GitHub rejects non-blocking `evaluate` mode on this plan, so activation follows two green bootstrap PRs and protects the first application PR. |
| **2** | Spec 1, task groups **1–3** | Scaffolding and the two contracts. The first real pull request, and the pipeline's first real exercise. |
| **3** | Spec 2, task groups **3, 5, 7, 8** | Coverage gates (now there is code to measure), Docker, and negative enforcement tests. |
| **4** | Spec 1, remaining groups | The safety controller and everything after, each PR now fully gated. |
| **5** | Specs 3 → 4 → 5 | In order. |

The only genuine circularity is that the coverage gate needs code to measure, which is why
exactly that part of spec 2 comes after spec 1's first slice. Everything else in spec 2 is
independent of the application.

**Specs 1–4 need no hardware.** Spec 5 is the only one that does, and it changes no application
logic — it supplies two drivers behind interfaces specs 1 and 3 already define. Spec 2's task
groups 9–11 are also hardware-blocked; groups 1–8 work today.

See [`HANDOFF.md`](HANDOFF.md) for the prompt to give an implementing agent.

---

## Why the build order is what it is

The hardware exists but is **~1 month out of reach** (as of Aug 2026). So the whole project is
built offline-first: every layer is testable against a **fake switcher** and **recorded video**,
and the real drivers are swap-in implementations of interfaces defined early and written last.

This is not a workaround — it is the better order regardless. The deterministic safety
controller is both the most important module (it's what makes the switcher look professional
rather than robotic) and the one that needs no hardware at all.

## Architecture — three layers, two contracts

The LLM is never in the video hot path. Each layer runs at its own rate.

```
  Perception  ──►  WorldState  ──►  Director  ──►  ShotIntent  ──►  Controller  ──►  ATEM
   5–15 Hz                          every few s                   deterministic    (one writer)
   no LLM                                                          hard vetoes
```

1. **Perception** — fast, local, no LLM. CV + audio VAD → a compact `WorldState`.
2. **Direction** — slow. Rule-based scorer (always on), local LLM (fast/offline tier), and
   Bedrock Claude (strategy tier). All emit a `ShotIntent` *suggestion*. None can touch the
   switcher.
3. **Execution + safety** — deterministic, the only writer to the ATEM.

`WorldState` and `ShotIntent` are the contracts that make the fake/real swap invisible.

**Run modes are the safety ladder:** `shadow` (log only) → `assist` (stage on preview, human
takes it) → `auto` (full control, with instant human override). Every shot is staged on preview
and held for a review window before promotion, so a human can always see what the AI intends
and veto it.

---

## Verified hardware facts

Researched Aug 2026 — these constrain the design and are easy to get wrong.

- **ATEM Constellation 2 M/E 4K** has no streaming engine and cannot route multiview to USB.
  The multiview SDI output is the only path for an AI feed.
- **DeckLink Duo 2 is 4 × 3G-SDI, 1080p60 max — no 4K.** A 4K multiview is therefore *not*
  capturable on this card. Plan: 1080p multiview, 4-up or 7-up layout (~960×540 per tile), with
  discrete per-camera capture on inputs 1–3 as the free upgrade path.
- It is a **PCIe card**, so any Apple Silicon Mac needs a Thunderbolt expansion chassis.
- **`pyatem` / OpenSwitcher, not PyATEMMax.** PyATEMMax is dormant since 2022, protocol-capped
  at ATEM firmware 7.5, has no per-field change events, and has an open unanswered issue titled
  *"Very unreliable connections to ATEM M2 Constellation"* — this exact hardware. pyatem targets
  firmware 8+ and emits `change:program-bus-input:0`, which is what takeover detection needs.
- **Capture path:** ffmpeg built `--enable-decklink` (Homebrew's stock ffmpeg does **not**
  include it — use the `homebrew-ffmpeg` tap), muxing raw video + PCM into **NUT on one pipe**,
  read with PyAV. The only path giving A/V on a shared hardware timebase.
- **Production target is an M1 Pro, 16 GB** → local LLM caps at ~4B params, and the detector
  belongs on the ANE via CoreML rather than the GPU, so it doesn't fight the LLM for memory
  bandwidth.

---

## Roadmap detail

### Spec 1 — `offline-switching-core` ✅

The `WorldState` and `ShotIntent` contracts, a `FakeSwitcher` that mirrors real ATEM bus
semantics, the deterministic safety controller (min-dwell, cooldown, cut-on-pause,
preview-then-promote, feed-health veto, hysteresis, rate cap, takeover detection, stale-data
failsafe), and a rule-based director. Runs end to end against scripted scenarios.

Dependencies: `pydantic` only. No hardware, no ML, no network.

### Spec 2 — `cicd-pipeline` 📝

GitHub Actions CI (ruff + pytest + coverage gates), repository rulesets so `main` can't be
pushed to directly, an Amazon-style PR review checklist, Docker for reproducible builds and
tests, and an Amazon-shaped promotion pipeline (alpha → beta → gamma → prod) built on GitHub
Environments.

### Spec 3 — `perception`

Turns video and audio into a real `WorldState`, replacing the hand-written scenarios.

Build order is easiest → hardest so the difficulty ramps:

1. **Feed health + motion** — pure OpenCV on 64×36 grayscale thumbnails, sub-millisecond. Black
   detection via `cv2.meanStdDev`; frozen detection needs *both* low `cv2.absdiff` **and** an
   identical frame hash for ≥3 frames, because a static-but-live camera still has sensor noise
   while a truly frozen feed repeats byte-identical frames.
2. **Audio VAD** — a pinned Silero ONNX model loaded directly with **onnxruntime**. The
   `silero-vad` Python package is intentionally not installed because it declares PyTorch and
   torchaudio dependencies. The pause grader treats 150–250 ms as a breath (don't cut),
   400 ms–1.2 s as a sentence boundary (the sweet spot), and >1.2 s as dead air.
3. **People** — MediaPipe **Tasks** API. ⚠️ `mediapipe.solutions` no longer exists; every
   tutorial using `mp.solutions.pose` is dead code.
4. **Framing** — arithmetic on detections, normalized by subject height so it's zoom-invariant.
   The highest-value feature is `wander_ratio` = net displacement / path length: near 1 means
   the speaker is walking across the stage (cut wide), far below 1 means gesturing in place
   (stay tight).

Also: `tools/make_multiview.py` to tile ordinary clips into a synthetic multiview, since the
real room isn't reachable.

### Spec 4 — `llm-directors`

- **Local tier** — `ollama serve` out-of-process, output constrained by
  `ShotIntent.model_json_schema()` so it's guaranteed parseable, `temperature=0`,
  `keep_alive=-1`. ~4B model on 16 GB.
- **Cloud tier** — AWS Bedrock, `AnthropicBedrockMantle`, model ID `anthropic.claude-opus-5`
  (Bedrock IDs carry the `anthropic.` prefix). Structured outputs work; prompt caching works but
  Bedrock has **no** automatic top-level `cache_control`, so put an explicit one on the last
  system block.
- **Cadence:** Bedrock sets *policy* every 20–30 s ("we're in the sermon body — favor tight
  shots, cut only on strong pauses"), the local tier picks the *shot* every few seconds, the
  deterministic controller decides *when*. ~5× cheaper than calling Bedrock every tick.
- Both tiers are advisory and **non-blocking**: fire async, keep the last decision, drop stale
  requests. A director two decisions behind is worse than no director.

### Spec 5 — `hardware-bringup`

Swaps two drivers and validates assumptions. Changes no application logic.

**Bench-test checklist — things that could not be verified from documentation:**

- [ ] Does the multiview SDI output carry **program audio**? Forum-sourced only, not in
      Blackmagic's specs. If not, route Program to a spare aux and capture that.
- [ ] Actual **per-tile pixel geometry** of the chosen multiview layout. Blackmagic publishes
      layouts, not tile dimensions. Measure and hard-code the crop rects.
- [ ] Is the Duo 2 in a **Thunderbolt PCIe chassis**? No Apple Silicon Mac except a Mac Pro has
      slots.
- [ ] Does **pyatem connect** to a Constellation 2 M/E 4K and report the right M/E topology?
- [ ] **M/E addressing** — confirm `index=0` drives M/E 1, in `assist` mode first.
- [ ] **Client slot count** — the ATEM allows only ~5 simultaneous clients; budget for one
      alongside ATEM Software Control and any physical panel.
- [ ] **Desktop Video version** — 16.2 is current and Apple Silicon native. There is at least
      one report of capture devices not being recognized on macOS 26 Tahoe; prefer macOS 15.

Then rehearse: `shadow` through a real service, diffing proposed cuts against what the human
operator actually did. Then `assist`. Only then `auto`.

---

## Working agreement

- **Claude** — research and specs. Verifies facts against real sources and flags what it could
  not verify.
- **Codex** — implementation, working from the spec sets.
- Code stays **beginner-readable**: plain, explicit Python, no clever abstractions. Specs should
  explain *why* a design choice was made, not just what to build.
- **Small pull requests.** One PR per implementation task group, with its tests in the same PR —
  never one large PR per spec. A 2,000-line diff gets rubber-stamped, which defeats the review
  gate entirely.
- **Hard stop at the end of each spec.** Report what landed and wait, rather than rolling into
  the next spec. Later specs build on contracts earlier ones define, and these stops are where a
  wrong contract gets caught cheaply.
- **Temporary files get cleaned up.** Scratch output goes outside the repo; anything left in the
  working tree should be an intended deliverable.
