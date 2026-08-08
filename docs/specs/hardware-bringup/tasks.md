# Tasks: Hardware Bring-Up

**BLOCKED until the ATEM and DeckLink are physically reachable (~Sept 2026).**

Work top to bottom. This spec is mostly verification, and the order is the safety mechanism —
each stage is proven on the bench before the next is attempted.

> **Delivery rhythm:** one pull request per implementation task group — **not** one large PR at
> the end. The bench-test groups (1–10) produce measurements recorded in `docs/BRINGUP.md` rather
> than code; commit those findings as you go so the record survives even if bring-up stalls.
> **Hard stop after each rehearsal service** (groups 11, 12, 13): report and wait before
> increasing the app's authority. Full rule in [`../HANDOFF.md`](../HANDOFF.md).

**Read `requirements.md` and `design.md` first.** Requirement numbers (R1–R13) are referenced
throughout.

**Prerequisites:** specs 1 and 3 implemented. Spec 2's pipeline is helpful but not required.

> ⚠️ **Nothing in Task Groups 1–9 may be attempted during a live service.** The bench checklist
> completes first. This is a real production switcher.

---

## Task Group 1: Physical prerequisites

- Confirm the DeckLink Duo 2 is installed in a **Thunderbolt PCIe expansion chassis** and visible
  to the Mac. It is a PCIe card; no Apple Silicon Mac except a Mac Pro has slots. If there is no
  chassis, stop — this is a blocking purchase (checklist item 3).
- Install **Blackmagic Desktop Video** (16.2 or current) and confirm the card appears in
  Blackmagic Desktop Video Setup.
- Note the macOS version. There is at least one report of capture devices not being recognized on
  macOS 26 Tahoe; macOS 15 is the safer target (checklist item 7).
- Record findings in `docs/BRINGUP.md`.

## Task Group 2: ffmpeg with DeckLink support

Homebrew's stock ffmpeg does **not** include decklink — the SDK is non-free, so the core formula
omits it.

```bash
brew install amiaopensource/amiaos/decklinksdk      # SDK headers FIRST — order matters
brew tap homebrew-ffmpeg/ffmpeg
brew install homebrew-ffmpeg/ffmpeg/ffmpeg --with-decklink
```

- Verify: `ffmpeg -f decklink -list_devices 1 -i dummy` lists the Duo 2's inputs.
- Verify: `ffmpeg -f decklink -list_formats 1 -i "<device name>"` shows supported formats.
- Confirm 1080p60 is the ceiling. **The Duo 2 is 3G-SDI and cannot capture 4K** — if a 4K
  multiview was configured on the ATEM, reconfigure it to 1080p.

## Task Group 3: Capture a frame (R2)

- Configure the ATEM's multiview to a **4-up or 7-up layout at 1080p**. Prefer fewer, larger
  tiles: 4-up gives ~960×540 per camera, 16-up gives ~480×270 which is marginal for pose.
- Route multiview to a Duo 2 input.
- Capture a few seconds to a file with ffmpeg and confirm the image is correct.
- `capture/decklink_source.py`: spawn one ffmpeg process muxing raw video + PCM into **NUT on a
  single pipe**, demux with PyAV, yield `CapturedFrame`.
  - Pipe `uyvy422`, convert with `cv2.COLOR_YUV2BGR_UYVY`. At 1080p60 `bgr24` is ~373 MB/s
    through the pipe versus ~249 MB/s — the conversion is cheaper than the copy.
  - **One process only.** A DeckLink device can be opened by exactly one process at a time.
  - Restart the subprocess with backoff on failure.

## Task Group 4: Measure tile geometry (R3)

- `tools/measure_tiles.py`: capture one frame, save it with a pixel-grid overlay.
- Read off the actual per-tile rectangles and write them into `config/hardware.yaml`, replacing
  spec 3's synthetic values (checklist item 2).
- Verify `extract_tiles` produces correct crops from a real multiview frame.

## Task Group 5: Confirm the audio source (R4)

- **Checklist item 1, the one most likely to surprise you.** Whether the multiview output carries
  program audio is forum-sourced and not in Blackmagic's published specs.
- Capture from the multiview input and inspect the audio stream. Is there audio? Is it the
  program mix?
- If **yes**: audio comes from the multiview capture, on the same timebase as video. Ideal.
- If **no**: route Program (or an aux carrying it) to a second Duo 2 input and capture that as
  the audio source. Note that this gives two processes and therefore two clocks — align on wall
  clock and record the added uncertainty in `docs/BRINGUP.md`.

## Task Group 6: Checkpoint — capture works

- Run spec 3's perception pipeline against `DeckLinkSource` instead of `FileSource`.
- **Perception must require no changes.** If it does, the `FrameSource` interface was wrong —
  fix it in spec 3, not here (R13).
- Dump `WorldState` JSONL from live capture and read it against what the cameras actually see.

## Task Group 7: Connect to the ATEM, read-only (R1, R5, R6)

Nothing writes to the switcher in this group.

- `execution/pyatem_client.py`: `PyatemSwitcher` implementing `SwitcherClient` with `pyatem`.
  - ⚠️ Call `switcher.loop()` **at least once per second** or the switcher drops the connection.
  - Subscribe to `change:program-bus-input:0`, `change:preview-bus-input:0`,
    `change:transition-position:0`.
  - Expose a `connected` state the controller can consult.
- Connect and **read only**: report switcher model and M/E topology. Confirm it matches an ATEM
  Constellation 2 M/E 4K (checklist item 4).
- Count how many client slots are free with ATEM Software Control also running (checklist item 6).
- Verify reads: change program by hand on the panel and confirm the driver sees it within a tick.

## Task Group 8: First writes, on the bench (R5)

Still not during a service.

- Command a **preview** change. Confirm it appears on the physical switcher. Preview is not on
  air, which is why this is the first write attempted.
- Confirm the M/E index: verify the index used drives **M/E 1** and not another mix effect
  (checklist item 5). Getting this wrong on air is the worst available outcome.
- Command a cut and confirm the buses swap as `FakeSwitcher` modelled them. If they don't, the
  fake was wrong — fix the fake so the existing test suite stays meaningful.

## Task Group 9: Resilience and latency (R7, R8, R9)

- **Pull the network cable** mid-run. Confirm: the controller is told, treats state as unknown,
  issues no commands, and resynchronizes on reconnect without a restart.
- **Pull the SDI cable** mid-run. Confirm the stale-data failsafe engages and the controller
  holds. Confirm recovery without a restart.
- **Measure end-to-end latency** from capture to switcher command. Record it. Review the
  controller's timing defaults against the real figure (checklist item 8).
- **Test takeover deliberately** (R12): operate the switcher by hand while the app runs, and
  confirm takeover detection engages within one tick against real hardware. It passing against
  the fake is not evidence it passes here.

## Task Group 10: Checkpoint — bench complete

- All eight bench-checklist items in `design.md` have measured answers recorded in
  `docs/BRINGUP.md`.
- The full existing test suite still passes unchanged against `FakeSwitcher` and `FileSource`
  (property 1).
- Confirm no `PyATEMMax` import anywhere (R13).

## Task Group 11: Shadow mode, one full service (R10, R11)

- Run in **`shadow`** mode through one complete service.
- Verify **zero switcher writes** — instrument it and assert, don't assume (R10).
- Log proposed decisions alongside what the human operator actually did (R11).
- Afterwards, diff the two. **This comparison is the real acceptance test for the whole
  project** — a passing test suite says the rules are implemented, this says the rules are
  right.
- Do not proceed until a service runs clean.

## Task Group 12: Assist mode, one full service (R10)

- Run in **`assist`**: the app stages shots on preview, a human takes them.
- The operator's experience is the signal here — are the staged shots ones they'd have chosen?
- Do not proceed until a service runs clean.

## Task Group 13: Auto mode

- Only after clean shadow and assist services.
- Have a human ready at the panel. Takeover detection is tested and working, but the first auto
  service is still supervised.
- `auto` SHALL NOT become a default in any committed config (R13).

## Task Group 14: Submit for review

- Branch first.
- Commit the drivers, `config/hardware.yaml` with real measured values, and `docs/BRINGUP.md`
  with the completed checklist.
- PR evidence: the shadow-mode decision log diffed against the operator's actual cuts, the
  measured latency figure, and confirmation the existing suite passes unchanged.
- **Clean up**: no captured media, no recorded footage, no scratch files in the tree (R13).
  Recorded services are valuable — store them outside the repo and note where.
