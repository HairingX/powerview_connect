"""Hunter Douglas / Luxaflex PowerView Gen 3 gateways, over their local HTTP API and event stream.

`PowerViewHub` follows a whole home and is what most code wants: it keeps every shade's state
current from the gateway's event stream and sends commands. Underneath, the client reads the
home and moves shades one rail at a time, and the tracker follows a shade's motion from the
events, polls and commands it is given.
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
    StreamIdleError,
)
from ._clock import Clock, SystemClock
from ._covers import CoverRole, CoverSpec, capability_name, covers
from ._hub import (
    Change,
    Connection,
    ConnectionChanged,
    GatewayChanged,
    GatewayState,
    HomeChanged,
    HomeDiff,
    PowerViewHub,
    ShadeChanged,
    ShadeState,
    Timing,
)
from ._model import (
    HARD_WIRED,
    RAILS,
    BatteryStatus,
    Capabilities,
    CommandFailure,
    EventKind,
    Gateway,
    GatewayStatus,
    Home,
    Positions,
    Rail,
    Scene,
    Shade,
    ShadeEvent,
    command_errors,
    parse_event,
    parse_gateway,
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
    "Change",
    "Clock",
    "CommandError",
    "CommandFailure",
    "Connection",
    "ConnectionChanged",
    "CoverRole",
    "CoverSpec",
    "EventKind",
    "Gateway",
    "GatewayChanged",
    "GatewayState",
    "GatewayStatus",
    "Home",
    "HomeChanged",
    "HomeDiff",
    "Positions",
    "PowerViewClient",
    "PowerViewError",
    "PowerViewHub",
    "Rail",
    "ReadOnlyError",
    "ResponseError",
    "Scene",
    "Shade",
    "ShadeChanged",
    "ShadeEvent",
    "ShadeState",
    "ShadeTracker",
    "StreamIdleError",
    "SystemClock",
    "Timing",
    "capability_name",
    "command_errors",
    "covers",
    "parse_event",
    "parse_gateway",
    "parse_home",
    "parse_positions",
    "parse_shade",
]
