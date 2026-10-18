"""Whether a cover stopped short of fully open, and the steps that raise it the rest of the way."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from ._covers import CoverRole, CoverSpec
from ._model import Rail
from ._motion import STEP


@dataclass(frozen=True, slots=True)
class StallRecovery:
    """How a cover that stopped short of fully open is raised the rest of the way.

    A cover sent fully open that stops at `min_openness` or above is raised to fully open in
    `steps` even steps, waiting `pause` seconds after each one has stopped.
    """

    min_openness: float = 0.8
    """The lowest openness counted as stuck; a stop below it is taken as meant."""
    steps: int = 3
    pause: float = 10.0

    def __post_init__(self) -> None:
        """Refuse settings that cannot work.

        Raises:
            ValueError: `min_openness` is outside 0.0 to below 1.0, `steps` is below 1, or
                `pause` is negative.
        """
        if not 0.0 <= self.min_openness < 1.0:
            raise ValueError(f"min_openness runs from 0.0 to below 1.0: {self}")
        if self.steps < 1 or self.pause < 0:
            raise ValueError(f"steps starts at 1 and pause at 0: {self}")


class StallOutcome(StrEnum):
    """Where a recovery of a stalled cover is."""

    RECOVERING = "recovering"
    """It has started."""
    RECOVERED = "recovered"
    """The cover is fully open."""
    GAVE_UP = "gave_up"
    """A step did not raise the cover, did not stop in time, or could not be sent."""
    INTERRUPTED = "interrupted"
    """Something else moved, stopped or removed the shade."""


def stalled_cover(
    shade_covers: Sequence[CoverSpec],
    target: Mapping[Rail, float],
    final: Mapping[Rail, float],
    settings: StallRecovery,
) -> tuple[CoverSpec, float] | None:
    """The cover that was sent fully open but stopped short, and how far it is open.

    Args:
        target: the positions the move was sent to.
        final: the positions it stopped at.

    Returns:
        None where no cover stalled.
    """
    for cover in shade_covers:
        if not cover.recoverable:
            continue
        aimed, reached = cover.openness(target), cover.openness(final)
        if aimed is None or reached is None or aimed < 1.0 - STEP:
            continue
        if not settings.min_openness <= reached < 1.0 - STEP:
            continue
        # The shade's firmware stops a bottom rail where it meets a lowered top rail.
        if cover.role is CoverRole.BOTTOM and final.get(Rail.SECONDARY, 0.0) > STEP:
            continue
        return cover, reached
    return None


def recovery_steps(start: float, steps: int) -> list[float]:
    """The openness of each step from `start` to fully open, leaving out steps that would not
    move the cover."""
    found: list[float] = []
    previous = start
    for step in range(1, steps + 1):
        value = 1.0 if step == steps else round(start + (1.0 - start) * step / steps, 2)
        if value > previous + STEP:
            found.append(value)
            previous = value
    return found
