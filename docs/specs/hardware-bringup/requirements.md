# Requirements: Hardware Bring-Up

Spec 5 of 5. Swaps the two fakes for real drivers and validates every assumption that could only
be checked against physical hardware.

**Blocked until the ATEM and DeckLink are reachable (~Sept 2026).**

**Depends on:** specs 1 and 3 for the `SwitcherClient` and `FrameSource` interfaces this
implements against.

**This spec changes no application logic.** If implementing it requires changing the controller,
a director, or perception, something was wrong in an earlier spec and should be fixed there.

---

## Requirement 1: Real switcher driver

The system SHALL provide `PyatemSwitcher` implementing the `SwitcherClient` protocol from
spec 1, backed by the `pyatem` / OpenSwitcher library.

WHEN `PyatemSwitcher` is substituted for `FakeSwitcher`
THEN the controller, directors, and application SHALL require no change.

WHEN the switcher's program or preview bus changes
THEN the driver SHALL surface the new state to the controller within one tick.

The driver SHALL service the library's event loop at least once per second, as the protocol
requires, or the switcher will drop the connection.

## Requirement 2: Real capture source

The system SHALL provide `DeckLinkSource` implementing the `FrameSource` protocol from spec 3.

WHEN frames and audio are captured
THEN they SHALL carry timestamps on a **shared timebase**, so an audio decision can be aligned
to a specific video frame.

WHEN `DeckLinkSource` is substituted for `FileSource`
THEN perception SHALL require no change.

The capture path SHALL use a single process holding the device, since a DeckLink device can be
opened by only one process at a time.

## Requirement 3: Measured tile geometry

Tile rectangles SHALL be measured against the real multiview output and recorded in
configuration.

WHEN the multiview layout is configured on the ATEM
THEN the actual per-tile pixel rectangles SHALL be measured from a captured frame and written to
the config file, replacing the synthetic values from spec 3.

## Requirement 4: Program audio source is confirmed

The system SHALL confirm where usable program audio actually comes from.

WHEN the multiview output is captured
THEN it SHALL be verified whether program audio is embedded in it.

WHEN program audio is not present on the multiview
THEN a Program or aux output SHALL be routed to a second capture input and used as the audio
source instead.

This is currently unverified against Blackmagic's published specifications and can only be
settled by measurement.

## Requirement 5: Switcher control is verified before it is trusted

The system SHALL verify basic switcher control before any automated mode is enabled.

WHEN the driver first connects
THEN it SHALL report the switcher model and M/E topology, and this SHALL be confirmed to match
an ATEM Constellation 2 M/E 4K.

WHEN a preview change is commanded
THEN the change SHALL be observed on the physical switcher before cut commands are attempted.

M/E addressing SHALL be confirmed empirically — that the index used drives M/E 1 and not another
mix effect.

## Requirement 6: Connection budget is respected

The system SHALL account for the ATEM's limited simultaneous client connections.

WHEN the application connects
THEN it SHALL occupy exactly one client slot, and SHALL disconnect cleanly on shutdown.

The application SHALL be usable alongside ATEM Software Control and a physical panel without
exhausting available connections.

## Requirement 7: Connection loss is survivable

The system SHALL handle losing the switcher without crashing or acting on stale state.

WHEN the connection to the switcher is lost
THEN the controller SHALL be told, SHALL treat switcher state as unknown, and SHALL NOT issue
commands.

WHEN the connection is restored
THEN the driver SHALL resynchronize state before any command is issued.

## Requirement 8: Capture loss is survivable

WHEN the capture device stops delivering frames
THEN the stale-data failsafe from spec 1 SHALL engage and the controller SHALL hold.

WHEN capture resumes
THEN normal operation SHALL resume without restarting the application.

## Requirement 9: Latency is measured, not assumed

The system SHALL measure end-to-end latency from capture to switcher command.

WHEN latency is measured
THEN the figure SHALL be recorded, and the controller's timing defaults SHALL be reviewed
against it.

## Requirement 10: Rehearsal ladder is mandatory

The system SHALL be exercised through the run modes in order, and SHALL NOT skip stages.

WHEN the system first runs against real hardware
THEN it SHALL run in `shadow` mode for at least one complete service.

WHEN shadow mode has run a clean service
THEN `assist` mode MAY be enabled, for at least one complete service.

WHEN assist mode has run a clean service
THEN `auto` mode MAY be enabled.

WHEN running in `shadow` mode
THEN the application SHALL perform zero switcher writes, and this SHALL be verified rather than
assumed.

## Requirement 11: Shadow mode produces a comparison

WHEN shadow mode runs during a service
THEN the system SHALL log its proposed decisions alongside what the human operator actually did,
so the two can be compared afterwards.

This comparison is the only real evidence that the system is ready for more autonomy.

## Requirement 12: Human override always wins

WHEN a human operates the switcher at any time in any mode
THEN the takeover detection from spec 1 SHALL engage against the real switcher, and the
application SHALL stop competing.

This SHALL be tested deliberately on real hardware, not assumed to work because it passed
against the fake.

## Requirement 13: No regressions (SHALL NOT)

This spec SHALL NOT modify `execution/controller.py`, the directors, or `perception/`. It
provides drivers behind interfaces those modules already consume.

This spec SHALL NOT introduce `PyATEMMax`. The project uses `pyatem` / OpenSwitcher, for reasons
recorded in `docs/specs/README.md`.

This spec SHALL NOT enable `auto` mode as a default in any configuration file.

This spec SHALL NOT be exercised on a live service before completing the bench-test checklist in
`tasks.md`.

This spec SHALL NOT leave temporary files, captured media, or recorded footage in the working
tree.
