# Design: Hardware Bring-Up

## Approach

Two new files implementing two existing interfaces, plus a disciplined validation sequence. If
the earlier specs did their job, this is the smallest spec in the project despite being the one
everyone thinks of as "the hard part."

The design principle is that **hardware is discovered, not assumed.** Several things below could
not be verified from documentation — Blackmagic publishes multiview layouts but not tile pixel
dimensions, and does not state whether the multiview output carries program audio. Those get
measured, and the measurements go into config rather than into code.

### Why `pyatem` and not `PyATEMMax`

`PyATEMMax` is already installed in the venv and is the more obvious choice, so the reasoning is
worth recording:

| | PyATEMMax | pyatem / OpenSwitcher |
|---|---|---|
| Last release | 2022 | 2024, packaged in several distros |
| Protocol ceiling | ATEM firmware **7.5** | firmware 8+ |
| Change events | one firehose `receive` callback with a 4-char code | **per-field**, e.g. `change:program-bus-input:0` |
| Constellation 2 M/E | open, unanswered issue: *"Very unreliable connections to ATEM M2 Constellation"* | no equivalent report found |
| Protocol docs | none | docs.openswitcher.org |

The per-field events are the deciding factor. Takeover detection needs to know *that program
changed* the moment it changes; with PyATEMMax that means polling or decoding a firehose. The
firmware ceiling is the second factor — this switcher runs 9.x/10.x, well past 7.5.

Keep PyATEMMax available as a fallback if pyatem turns out not to connect, but do not build on it.

### The capture path, and why it is shaped this way

```
DeckLink ──► ffmpeg (built --enable-decklink) ──► NUT on one pipe ──► PyAV ──► CapturedFrame
             raw video + PCM audio, one process        shared timebase
```

Three constraints force this shape:

1. **Homebrew's stock ffmpeg does not include decklink.** The SDK is non-free, so the core
   formula omits it. The `homebrew-ffmpeg` tap builds it with `--with-decklink`, and the
   DeckLink SDK headers must be installed **first** — order matters, the formula fails otherwise.
2. **Audio and video must share a timebase.** Muxing both into a single NUT stream on one pipe is
   what delivers that. Running two processes, or capturing audio separately through CoreAudio,
   gives two independent clocks and makes "cut 100 ms into this specific pause" meaningless.
3. **A DeckLink device can be opened by only one process at a time.** So one capture process,
   period.

Pipe `uyvy422` rather than `bgr24` and convert in Python with
`cv2.cvtColor(..., cv2.COLOR_YUV2BGR_UYVY)`. At 1080p60, `bgr24` over the pipe is roughly
373 MB/s versus 249 MB/s for `uyvy422` — the conversion is cheaper than the copy.

Expect roughly 40–80 ms glass-to-numpy. That is derived from a card-level measurement plus known
pipeline stages, not measured on this exact rig, which is why R9 requires measuring it.

### Hardware facts that constrain the configuration

- **DeckLink Duo 2 is 4 × 3G-SDI, 1080p60 maximum. It cannot capture 4K.** So a 4K multiview is
  not an option on this card regardless of what the ATEM can output. Use a 1080p multiview.
- With a **4-up** layout at 1080p, each tile is roughly 960×540 — workable for pose. A 16-up
  layout gives roughly 480×270, which is marginal. Prefer fewer, larger tiles.
- The Duo 2 is a **PCIe card**. No Apple Silicon Mac except a Mac Pro has slots, so a Thunderbolt
  expansion chassis is required. Confirm this exists before anything else.
- The four independent inputs are the upgrade path: if 960×540 tiles prove insufficient, move to
  discrete cameras on inputs 1–3 with a Program aux on input 4 for audio. Perception does not
  change — `extract_tiles` has a `discrete` passthrough mode for exactly this.
- **The ATEM allows only about five simultaneous client connections.** Budget one for this
  application alongside ATEM Software Control and any physical panel.

---

## Components / changes

- `execution/pyatem_client.py` — `PyatemSwitcher` implementing `SwitcherClient`. Owns the
  library's connection and services its loop at least once per second. Subscribes to
  `change:program-bus-input:0`, `change:preview-bus-input:0`, and `change:transition-position:0`.
  Exposes a `connected` state the controller can consult.
- `capture/decklink_source.py` — `DeckLinkSource` implementing `FrameSource`. Spawns the ffmpeg
  subprocess, demuxes NUT with PyAV, yields `CapturedFrame`. Restarts the subprocess on failure
  with backoff.
- `config/hardware.yaml` — measured tile rectangles, device names, multiview layout, audio
  source selection, ATEM address.
- `tools/measure_tiles.py` — captures one frame, saves it with a pixel grid overlay, to make
  reading off tile rectangles straightforward.
- `docs/BRINGUP.md` — the completed bench-test checklist with actual measured values, kept as a
  record.

---

## Do NOT touch

- `execution/controller.py`, `director/`, `perception/` — this spec provides drivers behind
  interfaces those already consume. **Needing to change them means an earlier spec was wrong**;
  fix it there rather than special-casing here.
- `execution/fake_switcher.py` and `capture/file_source.py` — they remain the test path and must
  keep working. Every existing test runs unchanged after this spec.

---

## Correctness properties

1. Every test written against `FakeSwitcher` and `FileSource` still passes unchanged.
2. In `shadow` mode against real hardware, the switcher's program and preview are never modified
   by the application.
3. Losing the switcher connection never produces a command based on stale state.
4. Losing capture engages the stale-data failsafe rather than acting on an old frame.
5. A human operating the switcher always causes takeover detection to engage within one tick.

---

## Verification strategy

The whole spec is verification, so the strategy is the sequence in `tasks.md`. The important
structural point: **each stage is verified on the bench before the next is attempted, and
nothing touches a live service until the bench checklist is complete.**

Order: chassis and drivers → ffmpeg build → capture a frame → measure tiles → confirm audio →
connect to ATEM read-only → command preview → command a cut on a bench switcher → shadow mode
for a full service → assist → auto.

The shadow-mode service is the real acceptance test. It produces a decision log that can be
diffed against what the human operator actually did (R11), and that diff — not a passing test
suite — is the evidence that the system is ready for more authority.

---

## Bench-test checklist — the unverified assumptions

These could not be settled from documentation. Each needs a measured answer recorded in
`docs/BRINGUP.md`.

| # | Question | Why it matters | If the answer is bad |
|---|---|---|---|
| 1 | Does the multiview SDI output carry **program audio**? | The VAD input depends on it. Forum-sourced only; not in Blackmagic's specs. | Route Program to a spare aux, capture on a second input |
| 2 | What are the **actual tile rectangles**? | Perception crops by these. Blackmagic publishes layouts, not pixel dimensions. | Measure and write to config |
| 3 | Is the Duo 2 in a **Thunderbolt PCIe chassis**? | Nothing works without it | Blocking purchase |
| 4 | Does **pyatem connect** and report the right M/E topology? | The whole control path | Fall back to PyATEMMax, accepting its limits |
| 5 | Does index `0` drive **M/E 1**? | Commanding the wrong M/E on air | Correct the index in config |
| 6 | How many **client slots** are free in practice? | Connecting may displace the operator's own tools | Reduce other connections |
| 7 | Does Desktop Video see the card on this **macOS version**? | There is at least one report of non-recognition on macOS 26 | Stay on macOS 15 |
| 8 | **End-to-end latency** | The controller's timing defaults assume it is small | Re-tune timing constants |

---

## Open questions

1. **Which capture configuration wins** — 1080p multiview on one input, or discrete cameras on
   three inputs plus a Program aux for audio? Multiview is simpler and gives hardware-synced
   audio for free; discrete gives full 1080p per camera. This can only be settled by seeing
   whether 960×540 tiles are enough for reliable pose landmarks. Start with multiview.
2. **Whether the ATEM's transition rate needs adjusting.** The controller commands cuts; if a
   dissolve is ever wanted, the ATEM's own transition rate becomes a tunable this project does
   not currently touch.
3. **How to record a service for offline replay.** Being able to capture a full service to disk
   would make every future tuning cycle vastly faster, and would remove the need for synthetic
   multiviews entirely. Worth building early during bring-up rather than late.
