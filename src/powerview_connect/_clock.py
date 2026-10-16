"""The time a hub follows motion and schedules its work by."""
from __future__ import annotations

import asyncio
import time
from typing import Protocol


class Clock(Protocol):
    """A monotonic clock and a way to wait on it."""

    def monotonic(self) -> float:
        """Seconds since some fixed point, never going back."""
        ...

    async def sleep(self, seconds: float) -> None:
        """Return once `seconds` have passed on this clock."""
        ...


class SystemClock:
    """The event loop's own time: `time.monotonic` and `asyncio.sleep`."""

    def monotonic(self) -> float:
        """Seconds of `time.monotonic`."""
        return time.monotonic()

    async def sleep(self, seconds: float) -> None:
        """Wait with `asyncio.sleep`."""
        await asyncio.sleep(seconds)
