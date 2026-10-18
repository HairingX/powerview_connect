"""Whether a cover stopped short of fully open, and the steps that raise it to the top."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ._covers import CoverRole, CoverSpec
from ._model import Rail
from ._motion import STEP


@dataclass(frozen=True, slots=True, kw_only=True)
class StalledOpenRetry:
    """How a cover sent fully open whose motor backed off short of the top is opened after all.

    A cover sent fully open that ends at `min_openness` or above, short of the top, is lowered to
    `lower_to` and raised in three steps, worked out from where the motor backed off to: two
    thirds of the way from there to the top, five sixths, and fully open, with `delay` seconds
    after each step that held. A step the motor backs off from
    starts a new round, up to `rounds` in all.
    """

    min_openness: float = 0.8
    """The lowest openness counted as backed off; a stop below it is taken as meant."""
    lower_to: float = 0.8
    """Where the cover is lowered to first: a motor that backed off was seen to take an opening
    from 0.81, and to refuse one from 0.85 and above."""
    rounds: int = 3
    """How many rounds are started at most."""
    delay: float = 300.0
    """Seconds after each step that held, for the fabric to settle."""

    def __post_init__(self) -> None:
        """Refuse settings that cannot work.

        Raises:
            ValueError: `min_openness` or `lower_to` is outside 0.0 to below 1.0, `rounds` is
                below 1, or `delay` is negative.
        """
        if not (0.0 <= self.min_openness < 1.0 and 0.0 <= self.lower_to < 1.0):
            raise ValueError(f"min_openness and lower_to run from 0.0 to below 1.0: {self}")
        if self.rounds < 1 or self.delay < 0:
            raise ValueError(f"rounds starts at 1 and delay at 0: {self}")


class RetryOutcome(StrEnum):
    """Where a retry of a stalled opening is."""

    STARTED = "started"
    OPENED = "opened"
    """The cover is fully open."""
    GAVE_UP = "gave_up"
    """A step did not raise the cover, or a move did not stop in time or could not be sent."""
    INTERRUPTED = "interrupted"
    """Something else moved or stopped the shade, removed it, or turned the retry off."""


def stalled_openness(
    cover: CoverSpec,
    target: Mapping[Rail, float],
    final: Mapping[Rail, float],
    settings: StalledOpenRetry,
) -> float | None:
    """How far `cover` is open where it was sent fully open but stopped short; None where it
    did not stall.

    Args:
        target: the positions the move was sent to.
        final: the positions it stopped at.
    """
    if not cover.retryable:
        return None
    aimed, reached = cover.openness(target), cover.openness(final)
    if aimed is None or reached is None or aimed < 1.0 - STEP:
        return None
    if not settings.min_openness <= reached < 1.0 - STEP:
        return None
    # The shade's firmware stops a bottom rail where it meets a lowered top rail.
    if cover.role is CoverRole.BOTTOM and final.get(Rail.SECONDARY, 0.0) > STEP:
        return None
    return reached



STEPS = (2 / 3, 5 / 6, 1.0)
"""How far each step goes, of the way from where the cover stopped to fully open: the motor was
seen to back off only on the last part of the way."""


def retry_steps(start: float) -> list[float]:
    """The openness of each step from `start` to fully open, leaving out steps that would not
    move the cover."""
    found: list[float] = []
    previous = start
    for share in STEPS:
        value = round(start + (1.0 - start) * share, 2)
        if value > previous + STEP:
            found.append(value)
            previous = value
    return found
