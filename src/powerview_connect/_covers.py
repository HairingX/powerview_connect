"""The covers a shade is controlled as, by its capabilities, and how far each is open."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from ._model import Capabilities, Rail, Shade

_TILTS = frozenset({
    Capabilities.BOTTOM_UP_TILT_90,
    Capabilities.BOTTOM_UP_TILT_180,
    Capabilities.VERTICAL_TILT_180,
})
_DUAL_OVERLAPPED = frozenset({
    Capabilities.DUAL_OVERLAPPED,
    Capabilities.DUAL_OVERLAPPED_TILT_90,
    Capabilities.DUAL_OVERLAPPED_TILT_180,
})
_VERTICAL = frozenset({Capabilities.VERTICAL, Capabilities.VERTICAL_TILT_180})
PLEATED_TYPES = frozenset({6, 7, 8, 9, 10, 33, 47})
"""The `type` of a pleated or cellular shade, whose fabric gathers as it opens; 6 and 8 measured."""


class CoverRole(StrEnum):
    """Which of a shade's covers a cover is."""

    SHADE = "shade"
    """The only cover of a shade with one rail."""
    TOP = "top"
    """The top rail of a top-down/bottom-up shade: 0.0 open at the top, 1.0 fully down."""
    BOTTOM = "bottom"
    """The bottom rail of a top-down/bottom-up shade."""
    FRONT = "front"
    """The front fabric of a dual-overlapped shade."""
    REAR = "rear"
    """The rear fabric of a dual-overlapped shade."""
    VANES = "vanes"
    """The vanes of a shade that only tilts."""


@dataclass(frozen=True, slots=True, kw_only=True)
class CoverSpec:
    """One cover of a shade: the rail it moves, and whether it also tilts the vanes.

    Openness runs from 0.0, closed, to 1.0, open. For a top-down/bottom-up shade's top rail,
    "open" is the rail at the top, which is what the gateway reports as 0.0.
    """

    role: CoverRole
    rail: Rail | None
    """None for vanes alone."""
    tilt: bool = False
    """The cover also tilts the vanes."""
    inverted: bool = False
    """The rail's 0.0 is open, as for a top-down shade, which opens downwards."""
    vertical: bool = False
    """The shade draws sideways, as a curtain does."""
    retryable: bool = False
    """An opening that stalls can be retried: the cover opens upwards, and its fabric
    gathers as it opens."""

    def openness(self, positions: Mapping[Rail, float]) -> float | None:
        """How far the cover's rail is open in `positions`; None without a rail or a value."""
        if self.rail is None or (value := positions.get(self.rail)) is None:
            return None
        return 1.0 - value if self.inverted else value

    def tilt_openness(self, positions: Mapping[Rail, float]) -> float | None:
        """How far the vanes are open in `positions`; None where the cover does not tilt."""
        return positions.get(Rail.TILT) if self.tilt or self.rail is None else None

    def closed(self, positions: Mapping[Rail, float]) -> bool | None:
        """Whether the cover is closed: its rail, or the vanes for vanes alone."""
        value = self.openness(positions) if self.rail is not None else positions.get(Rail.TILT)
        return None if value is None else round(value * 100) == 0

    def direction(self, directions: Mapping[Rail, int]) -> int:
        """1 while the cover opens, -1 while it closes, 0 otherwise."""
        rail = self.rail if self.rail is not None else Rail.TILT
        direction = directions.get(rail, 0)
        return -direction if self.inverted and self.rail is not None else direction

    def position(self, openness: float) -> dict[Rail, float]:
        """The positions that open the cover's rail to `openness`.

        Raises:
            ValueError: the cover has no rail, or `openness` is outside 0.0-1.0.
        """
        if self.rail is None:
            raise ValueError(f"the {self.role} cover has no rail to move")
        _check(openness)
        return {self.rail: 1.0 - openness if self.inverted else openness}

    def tilt_position(self, openness: float) -> dict[Rail, float]:
        """The positions that tilt the vanes to `openness`.

        Raises:
            ValueError: the cover does not tilt, or `openness` is outside 0.0-1.0.
        """
        if not self.tilt and self.rail is not None:
            raise ValueError(f"the {self.role} cover does not tilt")
        _check(openness)
        return {Rail.TILT: openness}


def _check(openness: float) -> None:
    if not 0.0 <= openness <= 1.0:
        raise ValueError(f"openness runs from 0.0 to 1.0: {openness}")


def covers(shade: Shade) -> tuple[CoverSpec, ...]:
    """The covers of `shade`, by its capabilities.

    Only bottom-up and top-down/bottom-up shades have been measured; the others follow the API
    description.
    """
    pleated = shade.type in PLEATED_TYPES
    capability = shade.capability
    match capability:
        case Capabilities.TOP_DOWN_BOTTOM_UP:
            return (CoverSpec(role=CoverRole.TOP, rail=Rail.SECONDARY),
                    CoverSpec(role=CoverRole.BOTTOM, rail=Rail.PRIMARY, retryable=pleated))
        case Capabilities.TOP_DOWN:
            return (CoverSpec(role=CoverRole.SHADE, rail=Rail.PRIMARY, inverted=True,
                              retryable=pleated),)
        case Capabilities.TILT_ONLY_180:
            return (CoverSpec(role=CoverRole.VANES, rail=None),)
        case _ if capability in _DUAL_OVERLAPPED:
            return (CoverSpec(role=CoverRole.FRONT, rail=Rail.PRIMARY,
                              tilt=capability is not Capabilities.DUAL_OVERLAPPED),
                    CoverSpec(role=CoverRole.REAR, rail=Rail.SECONDARY))
        case _:
            return (CoverSpec(role=CoverRole.SHADE, rail=Rail.PRIMARY, tilt=capability in _TILTS,
                              vertical=capability in _VERTICAL, retryable=pleated),)

