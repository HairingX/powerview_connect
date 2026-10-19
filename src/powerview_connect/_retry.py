"""Whether a cover stopped short of fully open, and how it is opened again."""
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

    A cover sent fully open that ends at `min_openness` or above, short of the top, is opened
    again once it has been still for `delay` seconds, in up to three attempts: fully open, then
    to `HOLD_AT` and held there for `delay` seconds before fully open, then fully open again. An
    attempt the motor refuses, moving the cover up by `MOVED` or less, does not count and is
    made again once the cover has been still for `delay` seconds.
    """

    min_openness: float = 0.8
    """The lowest openness counted as backed off; a stop below it is taken as meant, and a cover
    that backed off below it is left there."""
    delay: float = 300.0
    """Seconds the cover must have been still before each attempt, and how long it is held below
    the top."""

    def __post_init__(self) -> None:
        """Refuse settings that cannot work.

        Raises:
            ValueError: `min_openness` is outside 0.0 to below 1.0, or `delay` is negative.
        """
        if not 0.0 <= self.min_openness < 1.0:
            raise ValueError(f"min_openness runs from 0.0 to below 1.0: {self}")
        if self.delay < 0:
            raise ValueError(f"delay starts at 0: {self}")


HOLD_AT = 0.98
"""Where the second attempt holds the cover, for the fabric to fold, before it opens it fully."""
ATTEMPTS: tuple[tuple[float, ...], ...] = ((1.0,), (HOLD_AT, 1.0), (1.0,))
"""The openness each move of each attempt goes to."""
MOVED = 0.015
"""How far up a cover must move for an attempt to count: a refusing motor moves it 0.01 at
most."""


class RetryOutcome(StrEnum):
    """Where a retry of a stalled opening is."""

    STARTED = "started"
    OPENED = "opened"
    """The cover is fully open."""
    GAVE_UP = "gave_up"
    """Every attempt failed, the cover backed off below `min_openness`, or a move did not stop
    in time or could not be sent."""
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
