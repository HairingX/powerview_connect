"""Hunter Douglas / Luxaflex PowerView Gen 3 gateways, over their local HTTP API and event stream.

The client reads the home and moves shades, one rail at a time; the tracker follows a shade's
motion from the events, polls and commands it is given.
"""
from importlib.metadata import version as _installed_version

from ._client import (
    DEFAULT_TIMEOUT,
    CannotConnectError,
    CommandError,
    PowerViewClient,
    PowerViewError,
    ReadOnlyError,
    ResponseError,
)
from ._model import (
    HARD_WIRED,
    RAILS,
    BatteryStatus,
    Capabilities,
    EventKind,
    Gateway,
    Home,
    Positions,
    Rail,
    Scene,
    Shade,
    ShadeEvent,
    command_errors,
    parse_event,
    parse_home,
    parse_positions,
    parse_shade,
)
from ._motion import STEP, ShadeTracker

__version__ = _installed_version("powerview_connect")
__all__ = [
    "DEFAULT_TIMEOUT",
    "HARD_WIRED",
    "RAILS",
    "STEP",
    "BatteryStatus",
    "CannotConnectError",
    "Capabilities",
    "CommandError",
    "EventKind",
    "Gateway",
    "Home",
    "Positions",
    "PowerViewClient",
    "PowerViewError",
    "Rail",
    "ReadOnlyError",
    "ResponseError",
    "Scene",
    "Shade",
    "ShadeEvent",
    "ShadeTracker",
    "command_errors",
    "parse_event",
    "parse_home",
    "parse_positions",
    "parse_shade",
]
