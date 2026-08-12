"""Tests for the deterministic in-memory switcher."""

from atem_ai_vision_mixer.execution.fake_switcher import FakeSwitcher
from atem_ai_vision_mixer.execution.switcher import SwitcherClient


def _read_buses(switcher: SwitcherClient) -> tuple[int, int]:
    return switcher.program_input(), switcher.preview_input()


def test_fake_switcher_provides_the_switcher_bus_interface() -> None:
    """Verify the fake can be used through the hardware-neutral protocol."""
    switcher = FakeSwitcher(program=1, preview=2)

    assert _read_buses(switcher) == (1, 2)
    assert switcher.in_transition(now=0.0) is False


def test_set_preview_changes_only_preview_and_records_the_write() -> None:
    """Verify staging a shot leaves the on-air camera untouched."""
    switcher = FakeSwitcher(program=1, preview=2)

    switcher.set_preview(camera=3)

    assert _read_buses(switcher) == (1, 3)
    assert switcher.writes == ["set_preview:3"]


def test_cut_swaps_program_and_preview_and_records_the_write() -> None:
    """Verify an immediate cut follows real ATEM bus-swap semantics."""
    switcher = FakeSwitcher(program=1, preview=2)

    switcher.cut()

    assert _read_buses(switcher) == (2, 1)
    assert switcher.writes == ["cut"]


def test_auto_swaps_only_after_the_transition_duration() -> None:
    """Verify automatic transitions keep their buses stable until completion."""
    switcher = FakeSwitcher(
        program=1,
        preview=2,
        transition_duration_seconds=1.5,
    )

    switcher.auto(now=10.0)

    assert switcher.in_transition(now=10.0) is True
    assert switcher.in_transition(now=11.49) is True
    assert _read_buses(switcher) == (1, 2)

    assert switcher.in_transition(now=11.5) is False
    assert _read_buses(switcher) == (2, 1)
    assert switcher.writes == ["auto"]


def test_completed_auto_transition_swaps_the_buses_only_once() -> None:
    """Verify later transition polls cannot repeat a completed bus swap."""
    switcher = FakeSwitcher(
        program=1,
        preview=2,
        transition_duration_seconds=1.0,
    )
    switcher.auto(now=5.0)

    assert switcher.in_transition(now=6.0) is False
    assert switcher.in_transition(now=20.0) is False
    assert _read_buses(switcher) == (2, 1)


def test_cut_cancels_a_pending_auto_transition() -> None:
    """Verify a cut cannot leave a delayed second bus swap behind it."""
    switcher = FakeSwitcher(
        program=1,
        preview=2,
        transition_duration_seconds=3.0,
    )
    switcher.auto(now=10.0)

    switcher.cut()

    assert switcher.in_transition(now=20.0) is False
    assert _read_buses(switcher) == (2, 1)
    assert switcher.writes == ["auto", "cut"]


def test_operator_take_changes_program_without_recording_a_controller_write() -> None:
    """Verify human actions remain distinguishable from controller commands."""
    switcher = FakeSwitcher(program=1, preview=2)

    switcher.simulate_operator_take(camera=3)

    assert _read_buses(switcher) == (3, 2)
    assert switcher.writes == []


def test_operator_take_cancels_a_pending_auto_transition() -> None:
    """Verify an automatic transition cannot override a later human action."""
    switcher = FakeSwitcher(
        program=1,
        preview=2,
        transition_duration_seconds=2.0,
    )
    switcher.auto(now=10.0)

    switcher.simulate_operator_take(camera=3)

    assert switcher.in_transition(now=20.0) is False
    assert _read_buses(switcher) == (3, 2)
    assert switcher.writes == ["auto"]
