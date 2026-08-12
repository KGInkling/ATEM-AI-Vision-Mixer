"""The hardware-neutral interface used by the safety controller."""

from typing import Protocol


class SwitcherClient(Protocol):
    """The set of methods any switcher must provide — real or fake."""

    def program_input(self) -> int:
        """Return program so the controller can detect human takeovers."""
        ...

    def preview_input(self) -> int:
        """Return preview so the controller can detect a human veto."""
        ...

    def in_transition(self, now: float) -> bool:
        """Report transition state at ``now`` without reading an ambient clock."""
        ...

    def set_preview(self, camera: int) -> None:
        """Stage a camera on preview so a human can review it before a cut."""
        ...

    def cut(self) -> None:
        """Swap the buses immediately to promote the staged camera."""
        ...

    def auto(self, now: float) -> None:
        """Start a timed transition at ``now`` so completion is deterministic."""
        ...
