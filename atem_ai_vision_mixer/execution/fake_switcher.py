"""An in-memory switcher for developing without ATEM hardware."""

_DEFAULT_TRANSITION_DURATION_SECONDS = 1.0


class FakeSwitcher:
    """Mirror the ATEM bus behavior needed by the safety controller."""

    def __init__(
        self,
        program: int,
        preview: int,
        transition_duration_seconds: float = _DEFAULT_TRANSITION_DURATION_SECONDS,
    ) -> None:
        """Use explicit bus values so every test starts from known switcher state."""
        self._program = program
        self._preview = preview
        self._transition_duration_seconds = transition_duration_seconds
        self._transition_ends_at: float | None = None
        self.writes: list[str] = []

    def program_input(self) -> int:
        """Return program so the controller can detect human takeovers."""
        return self._program

    def preview_input(self) -> int:
        """Return preview so the controller can detect a human veto."""
        return self._preview

    def in_transition(self, now: float) -> bool:
        """Advance transition state from ``now`` so tests never read a real clock."""
        self._complete_transition_if_ready(now)
        return self._transition_ends_at is not None

    def set_preview(self, camera: int) -> None:
        """Stage a camera and record the command so write-path tests can inspect it."""
        self._preview = camera
        self.writes.append(f"set_preview:{camera}")

    def cut(self) -> None:
        """Swap buses now and cancel delayed work so a cut cannot happen twice."""
        self._transition_ends_at = None
        self._swap_buses()
        self.writes.append("cut")

    def auto(self, now: float) -> None:
        """Schedule a bus swap from ``now`` so transition tests stay deterministic."""
        self._transition_ends_at = now + self._transition_duration_seconds
        self.writes.append("auto")

    def simulate_operator_take(self, camera: int) -> None:
        """Change program outside the write log so takeover tests model a human."""
        self._transition_ends_at = None
        self._program = camera

    def _complete_transition_if_ready(self, now: float) -> None:
        if self._transition_ends_at is None:
            return

        if now < self._transition_ends_at:
            return

        self._transition_ends_at = None
        self._swap_buses()

    def _swap_buses(self) -> None:
        previous_program = self._program
        self._program = self._preview
        self._preview = previous_program
