"""Where a shade is, where it is heading, and whether it still moves."""
from __future__ import annotations

from ._model import RAILS, Positions, Rail, ShadeEvent

STEP = 0.005
"""Half the 0.01 steps positions are reported in: closer than this is the same position."""


class ShadeTracker:
    """The motion of one shade, from the gateway's events, polls and the commands sent to it.

    It does no I/O and reads no clock: every input carries the time, in seconds of a monotonic
    clock. A move starts with a command or a motion-started event and ends with whichever comes
    first: a motion-stopped event, the target reached and still for `settle` seconds, no change
    for `stall` seconds after it moved, nothing moving `start_grace` seconds after it started, or
    a timeout of at least `min_timeout` seconds. The motor starts some seconds after the command,
    and the gateway's estimate of a move's time is about half the real one, so neither ends a move.
    """

    def __init__(
        self,
        positions: Positions | None = None,
        *,
        stall: float = 6.0,
        settle: float = 1.5,
        start_grace: float = 25.0,
        min_timeout: float = 90.0,
    ) -> None:
        """A shade standing still at `positions`."""
        self.current: dict[Rail, float | None] = dict.fromkeys(RAILS)
        """Each rail's position, None until known."""
        self.target: dict[Rail, float | None] = dict.fromkeys(RAILS)
        """Each rail's target while moving, None where it is not known."""
        self.last_target: dict[Rail, float] = {}
        """The targets of the last move once it has ended, until the next one begins; emptied by
        a stop, as a stopped move did not aim where it ended."""
        self.moving = False
        self.eta: float | None = None
        """The gateway's estimate of the current move's time in seconds."""
        self._stall = stall
        self._settle = settle
        self._start_grace = start_grace
        self._min_timeout = min_timeout
        self._started_at = 0.0
        self._last_change_at = 0.0
        self._changed_since_start = False
        self._last_delta: dict[Rail, float] = {}
        if positions:
            self._apply(positions)

    def _apply(self, positions: Positions) -> bool:
        changed = False
        for rail, value in positions.items():
            old = self.current[rail]
            if old is None or value != old:
                if old is not None:
                    self._last_delta[rail] = value - old
                self.current[rail] = value
                changed = True
        return changed

    def _begin(self, now: float) -> None:
        self.moving = True
        self.target = dict.fromkeys(RAILS)
        self.last_target = {}
        self._started_at = now
        self._last_change_at = now
        self._changed_since_start = False
        self._last_delta = {}

    def _finish(self) -> None:
        if self.moving:
            self.last_target = {rail: value for rail, value in self.target.items()
                                if value is not None}
        self.moving = False
        self.target = dict.fromkeys(RAILS)
        self.eta = None

    def _reached(self) -> bool:
        targets = {rail: value for rail, value in self.target.items() if value is not None}
        for rail, value in targets.items():
            current = self.current[rail]
            if current is None or abs(current - value) > STEP:
                return False
        return bool(targets)

    def _timeout(self) -> float:
        return max(self._min_timeout, (self.eta or 0.0) * 3 + 30)

    def snapshot(self, positions: Positions) -> bool:
        """Take positions read while nothing was known to move; ignored during a move.

        Returns whether a position changed.
        """
        if self.moving:
            return False
        return self._apply(positions)

    def motion_started(self, event: ShadeEvent, now: float) -> None:
        """Start a move, or restart one, from a motion-started event."""
        self._apply(event.current)
        self._begin(now)
        self.target.update(event.target)
        self.eta = event.eta

    def motion_stopped(self, event: ShadeEvent) -> None:
        """End the move at the final positions of a motion-stopped event."""
        self._apply(event.current)
        self._finish()

    def command(self, now: float, targets: Positions) -> bool:
        """Note a move sent to the shade; returns whether the shade is expected to move.

        A command during a move keeps the other rails' targets.
        """
        expected = any(
            (current := self.current[rail]) is None or abs(current - value) > STEP
            for rail, value in targets.items()
        )
        if not expected:
            return False
        if self.moving:
            self._started_at = now
            self._last_change_at = now
        else:
            self._begin(now)
        self.target.update(targets)
        return True

    def stop_requested(self, now: float) -> None:
        """Note a stop sent to the shade: the move ends once the shade is still."""
        self.last_target = {}
        if self.moving:
            self.target = dict.fromkeys(RAILS)
            self._last_change_at = now

    def poll(self, positions: Positions, now: float) -> bool:
        """Take positions read during a move; returns whether the shade still moves."""
        if not self.moving:
            return False
        if self._apply(positions):
            self._last_change_at = now
            self._changed_since_start = True
        still_for = now - self._last_change_at
        if (
            (self._reached() and still_for >= self._settle)
            or (self._changed_since_start and still_for >= self._stall)
            or (not self._changed_since_start and now - self._started_at >= self._start_grace)
            or now - self._started_at >= self._timeout()
        ):
            self._finish()
        return self.moving

    def direction(self, rail: Rail) -> int:
        """1 while `rail` opens, -1 while it closes, 0 otherwise.

        Without a target, as after a stop, the direction is that of its last change.
        """
        if not self.moving:
            return 0
        target, current = self.target[rail], self.current[rail]
        if target is not None and current is not None:
            if target > current + STEP:
                return 1
            if target < current - STEP:
                return -1
            return 0
        delta = self._last_delta.get(rail, 0.0)
        return (delta > 0) - (delta < 0)
