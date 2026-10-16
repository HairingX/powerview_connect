"""What a PowerView Gen 3 gateway reports: its home, shades, scenes and shade events."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
from typing import cast

type JsonObject = dict[str, object]


class Rail(StrEnum):
    """A position a shade can be moved to, as the gateway names it."""

    PRIMARY = "primary"
    """The bottom rail of a top-down/bottom-up shade; the only rail of most others."""
    SECONDARY = "secondary"
    """The top rail of a top-down/bottom-up shade."""
    TILT = "tilt"
    """The vanes of a shade that tilts."""


RAILS: tuple[Rail, ...] = tuple(Rail)

type Positions = Mapping[Rail, float]
"""Rail positions from 0.0 (fully closed) to 1.0 (fully open); a rail not known is absent."""


class Capabilities(IntEnum):
    """What a shade can do, as the gateway's `capabilities` number says."""

    BOTTOM_UP = 0
    BOTTOM_UP_TILT_90 = 1
    BOTTOM_UP_TILT_180 = 2
    VERTICAL = 3
    VERTICAL_TILT_180 = 4
    TILT_ONLY_180 = 5
    TOP_DOWN = 6
    TOP_DOWN_BOTTOM_UP = 7
    DUAL_OVERLAPPED = 8
    DUAL_OVERLAPPED_TILT_90 = 9
    DUAL_OVERLAPPED_TILT_180 = 10


class BatteryStatus(IntEnum):
    """A shade's battery, as the gateway's `batteryStatus` number says.

    As Hunter Douglas's Gen 3 API describes it; a hard-wired shade was measured to report HIGH.
    """

    UNKNOWN = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    PLUGGED_IN = 4


HARD_WIRED = 1
"""The `powerType` of a shade on mains power, as measured."""


class EventKind(StrEnum):
    """The kinds of event the gateway's shade event stream sends."""

    MOTION_STARTED = "motion-started"
    """Sent for every move, whatever started it, with the target and an estimate of its time."""
    MOTION_STOPPED = "motion-stopped"
    """Sent when a move ends, with the final positions."""
    BATTERY_ALERT = "battery-alert"
    """Sent right before every move, by hard-wired shades too."""
    SHADE_ONLINE = "shade-online"
    SHADE_OFFLINE = "shade-offline"


@dataclass(frozen=True, slots=True)
class Gateway:
    """A gateway of the home: the primary one, or an auxiliary one."""

    serial: str
    name: str
    ip: str | None
    model: str | None
    firmware: str | None
    is_primary: bool
    id: int | None = None
    """The gateway's number in the home."""
    radio_firmware: tuple[str, ...] = ()
    shade_ids: tuple[int, ...] = ()
    """The shades the gateway serves."""


@dataclass(frozen=True, slots=True)
class GatewayStatus:
    """What a gateway reports of itself."""

    serial: str | None
    model: str | None
    brand: str | None
    hardware_version: str | None
    firmware: str | None
    radio_firmware: tuple[str, ...]
    is_primary: bool | None
    ip: str | None
    interface: str | None
    """The network interface in use: `eth0` is the wired one."""
    internet_state: str | None
    """`Connected` while the gateway reaches the internet."""

    @property
    def wifi(self) -> bool | None:
        """Whether the gateway is on Wi-Fi, or None for an interface this library does not know."""
        if self.interface is None:
            return None
        if self.interface.startswith("eth"):
            return False
        if self.interface.startswith("wlan"):
            return True
        return None

    @property
    def internet_connected(self) -> bool | None:
        """Whether the gateway reaches the internet, or None when it does not say."""
        return None if self.internet_state is None else self.internet_state == "Connected"


@dataclass(frozen=True, slots=True)
class Shade:
    """A shade, as the gateway last knew it."""

    id: int
    name: str
    type: int | None
    capabilities: int
    """The gateway's number; `capability` names it where it is known."""
    power_type: int | None
    """1 is hard-wired."""
    room_id: int | None
    room_name: str | None
    firmware: str | None
    ble_name: str | None
    serial: str | None
    positions: Positions = field(default_factory=lambda: {})
    signal_strength: int | None = None
    """The gateway's reception of the shade, in dBm; closer to 0 is stronger."""
    battery_status: int | None = None
    """The gateway's number for the battery; hard-wired shades report one too."""

    @property
    def hard_wired(self) -> bool:
        """Whether the shade runs on mains power, so its battery status means nothing."""
        return self.power_type == HARD_WIRED

    @property
    def has_battery(self) -> bool:
        """Whether the shade reports a power type other than mains power, so it has a battery."""
        return self.power_type is not None and not self.hard_wired

    @property
    def battery_low(self) -> bool | None:
        """Whether the battery is low; None without a battery or a known status."""
        battery = self.battery
        if not self.has_battery or battery in (None, BatteryStatus.UNKNOWN):
            return None
        return battery is BatteryStatus.LOW

    @property
    def battery(self) -> BatteryStatus | None:
        """The battery status named, or None for a number this library does not know."""
        try:
            return BatteryStatus(self.battery_status) if self.battery_status is not None else None
        except ValueError:
            return None

    @property
    def capability(self) -> Capabilities | None:
        """The capabilities named, or None for a number this library does not know."""
        try:
            return Capabilities(self.capabilities)
        except ValueError:
            return None

    @property
    def capability_name(self) -> str:
        """A name for the capabilities, such as "Top Down Bottom Up", or "Capabilities 42"."""
        capability = self.capability
        if capability is None:
            return f"Capabilities {self.capabilities}"
        return capability.name.replace("_", " ").title()


@dataclass(frozen=True, slots=True)
class Scene:
    """A scene stored on the gateway."""

    id: int
    name: str
    room_id: int | None


@dataclass(frozen=True, slots=True)
class Home:
    """The gateway's whole home: its gateways, shades and scenes."""

    home_id: str | None
    gateways: tuple[Gateway, ...]
    shades: Mapping[int, Shade]
    scenes: tuple[Scene, ...]

    @property
    def primary_gateway(self) -> Gateway | None:
        """The gateway that is not an auxiliary one, if listed."""
        return next((gateway for gateway in self.gateways if gateway.is_primary), None)

    def gateway_of(self, shade_id: int) -> Gateway | None:
        """The gateway that lists `shade_id` among the shades it serves, if any does."""
        return next((gateway for gateway in self.gateways if shade_id in gateway.shade_ids), None)

    def gateway_at(self, host: str) -> Gateway | None:
        """The gateway listed with the address of `host`, which may carry a port."""
        address = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
        return next((gateway for gateway in self.gateways if gateway.ip == address), None)


@dataclass(frozen=True, slots=True)
class ShadeEvent:
    """One event from the gateway's shade event stream."""

    kind: str
    """One of `EventKind`, or a kind this library does not know."""
    shade_id: int
    current: Positions
    target: Positions
    """Only for `EventKind.MOTION_STARTED`."""
    eta: float | None
    """The gateway's estimate of a move's time in seconds; real moves take about twice as long."""


def _object(value: object) -> JsonObject | None:
    return cast(JsonObject, value) if isinstance(value, dict) else None


def _objects(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [found for item in cast(list[object], value) if (found := _object(item)) is not None]


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def parse_positions(raw: object) -> dict[Rail, float]:
    """The rail positions in a positions object, each limited to 0.0-1.0; others are left out."""
    found = _object(raw) or {}
    positions: dict[Rail, float] = {}
    for rail in RAILS:
        value = _number(found.get(rail))
        if value is not None:
            positions[rail] = min(1.0, max(0.0, value))
    return positions


def _ints(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(found for item in cast(list[object], value) if (found := _int(item)) is not None)


def _firmware(raw: object) -> str | None:
    found = _object(raw)
    if found is None:
        return None
    parts = [_int(found.get(key)) for key in ("revision", "subRevision", "build")]
    if any(part is None for part in parts):
        return None
    return ".".join(str(part) for part in parts)


def parse_shade(raw: Mapping[str, object], room_name: str | None = None) -> Shade:
    """A shade from its object, as `/home` and `/home/shades/{id}` give it.

    Raises:
        ValueError: the object has no shade id.
    """
    shade_id = _int(raw.get("id"))
    if shade_id is None:
        raise ValueError(f"a shade without an id: {dict(raw)!r}")
    return Shade(
        id=shade_id,
        name=_text(raw.get("ptName")) or _text(raw.get("bleName")) or f"Shade {shade_id}",
        type=_int(raw.get("type")),
        capabilities=_int(raw.get("capabilities")) or 0,
        power_type=_int(raw.get("powerType")),
        room_id=_int(raw.get("roomId")),
        room_name=room_name,
        firmware=_firmware(raw.get("firmware")),
        ble_name=_text(raw.get("bleName")),
        serial=_text(raw.get("serialNumber")),
        positions=parse_positions(raw.get("positions")),
        signal_strength=_int(raw.get("signalStrength")),
        battery_status=_int(raw.get("batteryStatus")),
    )


def parse_home(raw: Mapping[str, object]) -> Home:
    """The home from the `/home` document; entries without an id are left out."""
    gateways = tuple(
        Gateway(
            serial=serial,
            name=_text(gateway.get("name")) or "PowerView Gateway",
            ip=_text(gateway.get("ip")),
            model=_text(gateway.get("model")),
            firmware=_text(gateway.get("v")),
            is_primary=gateway.get("aux") is not True,
            id=_int(gateway.get("_id")),
            radio_firmware=tuple(
                radio for key in ("radio1V", "radio2V") if (radio := _text(gateway.get(key)))),
            shade_ids=_ints(gateway.get("shd_Ids")),
        )
        for gateway in _objects(raw.get("gateways"))
        if (serial := _text(gateway.get("serial"))) is not None
    )
    shades: dict[int, Shade] = {}
    for room in _objects(raw.get("rooms")):
        for shade in _objects(room.get("shades")):
            if _int(shade.get("id")) is not None:
                parsed = parse_shade(shade, _text(room.get("name")))
                shades[parsed.id] = parsed
    scenes = tuple(
        Scene(id=scene_id, name=_text(scene.get("name")) or f"Scene {scene_id}",
              room_id=_int(scene.get("room_Id")))
        for scene in _objects(raw.get("scenes"))
        if (scene_id := _int(scene.get("_id"))) is not None
    )
    home = _object(raw.get("home")) or {}
    return Home(
        home_id=_text(raw.get("id")) or _text(home.get("key")),
        gateways=gateways,
        shades=shades,
        scenes=scenes,
    )


def parse_gateway(raw: Mapping[str, object]) -> GatewayStatus:
    """What a gateway reports of itself, from the `/gateway` document."""
    config = _object(raw.get("config")) or {}
    firmware = _object(config.get("firmware")) or {}
    network = _object(config.get("networkStatus")) or {}
    primary = (_object(config.get("mgwConfig")) or {}).get("primary")
    return GatewayStatus(
        serial=_text(config.get("serialNumber")),
        model=_text(config.get("model")),
        brand=_text(config.get("brand")),
        hardware_version=_text(config.get("hwVersion")),
        firmware=_firmware(firmware.get("mainProcessor")),
        radio_firmware=tuple(
            found for radio in _objects(firmware.get("radios"))
            if (found := _firmware(radio)) is not None),
        is_primary=primary if isinstance(primary, bool) else None,
        ip=_text(network.get("ipAddress")) or _text(config.get("ip")),
        interface=_text(network.get("activeInterface")),
        internet_state=_text(network.get("internetState")),
    )


def parse_event(line: str) -> ShadeEvent | None:
    """The shade event on one line of the event stream, or None for any other line.

    The stream opens with a status line, `100 HELO`, then sends `data: {json}` lines separated
    by blank lines.
    """
    line = line.strip()
    if not line.startswith("data:"):
        return None
    try:
        payload = _object(json.loads(line.removeprefix("data:")))
    except ValueError:
        return None
    if payload is None:
        return None
    kind, shade_id = _text(payload.get("evt")), _int(payload.get("id"))
    if kind is None or shade_id is None:
        return None
    target = _object(payload.get("targetPositions")) or {}
    return ShadeEvent(
        kind=kind,
        shade_id=shade_id,
        current=parse_positions(payload.get("currentPositions")),
        target=parse_positions(target),
        eta=_number(target.get("etaInSeconds")),
    )


@dataclass(frozen=True, slots=True)
class CommandFailure:
    """One error the gateway reported for a command."""

    shade_id: int | None
    """The shade it is for; None for an error of the gateway itself."""
    code: object
    """The gateway's `err` value."""

    def __str__(self) -> str:
        """The failure, naming the shade by its id."""
        where = "gateway" if self.shade_id is None else f"shade {self.shade_id}"
        return f"{where} err={self.code}"


def command_errors(response: object) -> list[CommandFailure]:
    """What went wrong, by the answer to a move or a stop; empty when nothing did.

    The gateway answers `{"err": 0, "responses": [{"id", "bleName", "err", "hex"}], ...}`.
    """
    found = _object(response)
    if found is None:
        return []
    failures: list[CommandFailure] = []
    if found.get("err") not in (None, 0):
        failures.append(CommandFailure(None, found.get("err")))
    for item in _objects(found.get("responses")):
        if item.get("err") not in (None, 0):
            failures.append(CommandFailure(_int(item.get("id")), item.get("err")))
    return failures
