"""Whether a cover stopped short of fully open, and how a retry of its opening goes."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ._covers import CoverRole, CoverSpec
from ._model import Rail
from ._motion import STEP


@dataclass(frozen=True, slots=True, kw_only=True)
class StalledOpenRetry:
    """How a cover whose opening stalled is opened after all.

    A cover sent fully open that stops at `min_openness` or above is lowered by `lower_by` and,
    as soon as it has stopped, sent fully open again in one move; up to `attempts` times.
    """

    min_openness: float = 0.8
    """The lowest openness counted as stalled; a stop below it is taken as meant."""
    lower_by: float = 0.1
    """How far the cover is lowered before each new opening: a motor that stalled opens again
    only from a little lower down."""
    attempts: int = 2
    """How many times the cover is lowered and opened before the retry gives up."""

    def __post_init__(self) -> None:
        """Refuse settings that cannot work.

        Raises:
            ValueError: `min_openness` is outside 0.0 to below 1.0, `lower_by` is outside
                above 0.0 to 1.0, or `attempts` is below 1.
        """
        if not 0.0 <= self.min_openness < 1.0:
            raise ValueError(f"min_openness runs from 0.0 to below 1.0: {self}")
        if not 0.0 < self.lower_by <= 1.0:
            raise ValueError(f"lower_by runs from above 0.0 to 1.0: {self}")
        if self.attempts < 1:
            raise ValueError(f"attempts starts at 1: {self}")


class RetryOutcome(StrEnum):
    """Where a retry of a stalled opening is."""

    STARTED = "started"
    OPENED = "opened"
    """The cover is fully open."""
    GAVE_UP = "gave_up"
    """No attempt opened the cover fully, or a move did not stop in time or could not be sent."""
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

