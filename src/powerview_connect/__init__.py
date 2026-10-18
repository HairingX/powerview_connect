"""Hunter Douglas / Luxaflex PowerView Gen 3 homes, over the gateway's local HTTP API.

`PowerViewHub` follows a whole home through its primary gateway: it keeps every shade's and
gateway's state current from the gateway's event stream, tells listeners of each change, and
sends commands. Everything else here describes what it knows.
"""
from importlib.metadata import version as _installed_version

from ._client import CannotConnectError, CommandError, PowerViewError, ReadOnlyError, ResponseError
from ._clock import Clock
from ._covers import CoverRole, CoverSpec
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
    ShadeStalled,
    ShadeState,
    Timing,
)
from ._model import (
    BatteryStatus,
    Capabilities,
    CommandFailure,
    Gateway,
    GatewayStatus,
    Home,
    Rail,
    Scene,
    Shade,
)
from ._retry import RetryOutcome, StalledOpenRetry

__version__ = _installed_version("powerview_connect")
__all__ = [
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
    "Gateway",
    "GatewayChanged",
    "GatewayState",
    "GatewayStatus",
    "Home",
    "HomeChanged",
    "HomeDiff",
    "PowerViewError",
    "PowerViewHub",
    "Rail",
    "ReadOnlyError",
    "ResponseError",
    "RetryOutcome",
    "Scene",
    "Shade",
    "ShadeChanged",
    "ShadeStalled",
    "ShadeState",
    "StalledOpenRetry",
    "Timing",
]
