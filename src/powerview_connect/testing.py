"""A simulated PowerView Gen 3 gateway, for tests of code that uses one.

It is an HTTP server on 127.0.0.1 that answers as a real Gen 3 gateway was measured to: a move
changes only the rails it names, a top-down/bottom-up shade's rails stop where they meet short
of the target announced, every move is announced on the event stream by a battery alert and a
motion-started event, and a shade's positions change only when the test moves it. The test decides when a move progresses
and ends, when streams drop and when the gateway goes offline.
"""
from __future__ import annotations

import asyncio
import contextlib
import heapq
import json
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Self, cast

from aiohttp import web

from ._model import RAILS, Capabilities, EventKind, Rail

__all__ = ["ManualClock", "Request", "SimulatedGateway", "SimulatedShade"]

_BREAK = "break"
"""Put on a stream's queue to cut the stream off."""

_ETA_PER_FULL_MOVE = 20.0
"""The gateway's estimate, in seconds, for a rail moving all the way."""


class ManualClock:
    """A clock that moves only when a test advances it, for a hub's `clock`."""

    def __init__(self, now: float = 1000.0) -> None:
        """A clock at `now` seconds."""
        self.now = now
        self._sleepers: list[tuple[float, int, asyncio.Future[None]]] = []
        self._count = 0

    def monotonic(self) -> float:
        """The time, in seconds."""
        return self.now

    async def sleep(self, seconds: float) -> None:
        """Return once the clock has been advanced by `seconds`."""
        future = asyncio.get_running_loop().create_future()
        self._count += 1
        heapq.heappush(self._sleepers, (self.now + max(seconds, 0.0), self._count, future))
        await future

    @property
    def sleepers(self) -> int:
        """How many sleeps wait for the clock."""
        return sum(not future.done() for _, _, future in self._sleepers)

    async def advance(self, seconds: float) -> None:
        """Move the clock on, waking each sleep in the order it is due."""
        until = self.now + seconds
        while self._sleepers and self._sleepers[0][0] <= until:
            due, _, future = heapq.heappop(self._sleepers)
            self.now = max(self.now, due)
            if not future.done():
                future.set_result(None)
                await asyncio.sleep(0)
        self.now = until
        await asyncio.sleep(0)


@dataclass(frozen=True, slots=True)
class Request:
    """A request the simulated gateway received."""

    method: str
    path: str
    query: Mapping[str, str]
    body: object


@dataclass(slots=True)
class SimulatedShade:
    """A shade of the simulated gateway; change it to change what the gateway reports."""

    id: int
    name: str
    room_id: int
    capabilities: int
    type: int
    power_type: int = 1
    positions: dict[Rail, float] = field(default_factory=lambda: dict.fromkeys(RAILS, 0.0))
    target: dict[Rail, float] | None = None
    """Where the shade is heading, None while it stands still."""
    firmware: tuple[int, int, int] = (3, 0, 433)
    serial: str | None = None
    signal_strength: int | None = -66
    """In dBm, as the gateway reports it; None leaves it out."""
    battery_status: int = 3
    gateway: str | None = None
    """The serial number of the gateway that serves the shade; None for the simulated one."""

    @property
    def ble_name(self) -> str:
        """The shade's Bluetooth name, as the gateway reports it."""
        return f"SIM:{self.id:04d}"

    def document(self) -> dict[str, object]:
        """The shade as `/home` and `/home/shades/{id}` give it."""
        revision, sub_revision, build = self.firmware
        document: dict[str, object] = {
            "id": self.id,
            "type": self.type,
            "ptName": self.name,
            "capabilities": self.capabilities,
            "powerType": self.power_type,
            "roomId": self.room_id,
            "firmware": {"revision": revision, "subRevision": sub_revision, "build": build},
            "positions": {**self.positions, "velocity": 0},
            "bleName": self.ble_name,
            "batteryStatus": self.battery_status,
        }
        if self.serial is not None:
            document["serialNumber"] = self.serial
        if self.signal_strength is not None:
            document["signalStrength"] = self.signal_strength
        return document


@dataclass(slots=True)
class _Gateway:
    id: int
    serial: str
    name: str
    ip: str
    aux: bool
    model: str = "Pro"
    firmware: str = "3.2.59"
    radio_firmware: str = "3.0.43"


@dataclass(slots=True)
class _Scene:
    id: int
    name: str
    room_id: int
    members: dict[int, dict[Rail, float]]


def _iso_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class SimulatedGateway:
    """A Gen 3 gateway with no shades, rooms or scenes until a test adds them.

    Use it as an async context manager, or call `start` and `close`.
    """

    def __init__(
        self,
        *,
        serial: str = "SIMULATED-0001",
        name: str = "Simulated Gateway",
        home_id: str = "simulated-home",
        primary: bool = True,
    ) -> None:
        """A gateway that lists itself at 127.0.0.1, as the primary gateway unless `primary` is
        False."""
        self.gateways: list[_Gateway] = [_Gateway(1, serial, name, "127.0.0.1", aux=not primary)]
        self.interface = "eth0"
        """The network interface the gateway reports in use."""
        self.internet_state = "Connected"
        """What the gateway reports of its internet connection."""
        self.refuse_streams = False
        """Answer a request for the event stream with HTTP 503."""
        self.shade_read_delay = 0.0
        """Seconds a read of one shade waits before it is answered, as on a busy gateway."""
        self.home_id = home_id
        self.rooms: dict[int, str] = {}
        self.shades: dict[int, SimulatedShade] = {}
        self.scenes: dict[int, _Scene] = {}
        self.requests: list[Request] = []
        """Every request received, oldest first."""
        self.command_errors: dict[int, int] = {}
        """Shade id to the error code the gateway reports for a command to that shade."""
        self._streams: list[asyncio.Queue[str | None]] = []
        self._stream_opened = asyncio.Event()
        self._runner: web.AppRunner | None = None
        self._port = 0
        self._app = web.Application()
        self._app.router.add_get("/home", self._home)
        self._app.router.add_get("/gateway", self._gateway)
        self._app.router.add_get("/home/shades", self._all_shades)
        self._app.router.add_get("/home/shades/events", self._events)
        self._app.router.add_get("/home/shades/{id}", self._shade)
        self._app.router.add_put("/home/shades/positions", self._positions)
        self._app.router.add_put("/home/shades/stop", self._stop)
        self._app.router.add_put("/home/scenes/{id}/activate", self._activate)
        self._app.router.add_put("/home/shades/{id}/motion", self._motion)

    # ------------------------------------------------------------------- what it holds

    def add_gateway(self, serial: str, name: str, *, ip: str, aux: bool = True) -> None:
        """List another gateway in the home, an auxiliary one unless `aux` is False.

        `ip` may carry a port, to list another simulated gateway by its `host`.
        """
        self.gateways.append(_Gateway(len(self.gateways) + 1, serial, name, ip, aux))

    def add_room(self, room_id: int, name: str) -> None:
        """Add a room for shades and scenes to be in."""
        self.rooms[room_id] = name

    def add_shade(
        self,
        shade_id: int,
        name: str,
        *,
        room_id: int,
        capabilities: int = Capabilities.BOTTOM_UP,
        shade_type: int = 6,
        positions: Mapping[Rail, float] | None = None,
        power_type: int = 1,
        firmware: tuple[int, int, int] = (3, 0, 433),
        serial: str | None = None,
        signal_strength: int | None = -66,
        battery_status: int = 3,
        gateway: str | None = None,
    ) -> SimulatedShade:
        """Add a shade to a room, standing still at `positions` (0.0 for a rail not given).

        `gateway` is the serial number of the gateway that serves it; None for this one.
        """
        shade = SimulatedShade(
            shade_id, name, room_id, int(capabilities), shade_type,
            power_type=power_type, firmware=firmware, serial=serial,
            signal_strength=signal_strength, battery_status=battery_status, gateway=gateway,
        )
        shade.positions.update(positions or {})
        self.shades[shade_id] = shade
        return shade

    def remove_shade(self, shade_id: int) -> None:
        """Remove a shade from the home."""
        del self.shades[shade_id]

    def add_scene(
        self, scene_id: int, name: str, *, room_id: int, members: Mapping[int, Mapping[Rail, float]]
    ) -> None:
        """Add a scene moving each member shade to its positions."""
        self.scenes[scene_id] = _Scene(
            scene_id, name, room_id, {shade: dict(positions) for shade, positions in members.items()}
        )

    def home(self) -> dict[str, object]:
        """The `/home` document."""
        return {
            "id": self.home_id,
            "gateways": [
                {"_id": gateway.id, "v": gateway.firmware, "serial": gateway.serial,
                 "model": gateway.model, "name": gateway.name, "ip": gateway.ip,
                 "aux": gateway.aux, "radio1V": gateway.radio_firmware,
                 "radio2V": gateway.radio_firmware,
                 "shd_Ids": [shade.id for shade in self.shades.values()
                             if (shade.gateway or self.gateways[0].serial) == gateway.serial]}
                for gateway in self.gateways
            ],
            "rooms": [
                {"_id": room_id, "name": name,
                 "shades": [shade.document() for shade in self.shades.values() if shade.room_id == room_id]}
                for room_id, name in self.rooms.items()
            ],
            "scenes": [
                {"_id": scene.id, "name": scene.name, "room_Id": scene.room_id,
                 "members": [
                     {"shd_Id": shade_id,
                      "pos": {"pos1": round(positions.get(Rail.PRIMARY, 0.0) * 10000),
                              "pos2": round(positions.get(Rail.SECONDARY, 0.0) * 10000)}}
                     for shade_id, positions in scene.members.items()
                 ]}
                for scene in self.scenes.values()
            ],
        }

    def gateway(self) -> dict[str, object]:
        """The `/gateway` document, of the simulated gateway itself."""
        own = self.gateways[0]
        revision, sub_revision, build = (int(part) for part in own.firmware.split("."))
        radio = dict(zip(("revision", "subRevision", "build"),
                         (int(part) for part in own.radio_firmware.split(".")), strict=True))
        return {
            "config": {
                "hwVersion": "3.0.4", "model": own.model, "brand": "Luxaflex",
                "serialNumber": own.serial,
                "firmware": {
                    "mainProcessor": {"revision": revision, "subRevision": sub_revision,
                                      "build": build, "name": "PV_Gen3"},
                    "radios": [radio, radio],
                },
                "networkStatus": {
                    "ipAddress": own.ip, "activeInterface": self.interface,
                    "internetState": self.internet_state, "ssid": "",
                },
                "mgwConfig": {"primary": not own.aux},
                "mgwStatus": {"running": True},
                "ip": own.ip,
            },
            "manifest": {"version": own.firmware},
        }

    # ---------------------------------------------------------------------- running

    @property
    def host(self) -> str:
        """The address a client reaches the gateway at, with its port."""
        if self._port == 0:
            raise RuntimeError("the simulated gateway is not started")
        return f"127.0.0.1:{self._port}"

    async def start(self) -> None:
        """Start answering, at the port it had before if it was started before."""
        listening = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listening.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listening.bind(("127.0.0.1", self._port))
        self._port = cast(tuple[str, int], listening.getsockname())[1]
        # A stream's handler ends when its client goes, so `streams` counts only open ones.
        runner = web.AppRunner(self._app, handle_signals=False, handler_cancellation=True,
                               shutdown_timeout=0.1)
        await runner.setup()
        await web.SockSite(runner, listening).start()
        self._runner = runner

    async def close(self) -> None:
        """Close every stream and stop answering; `start` brings it back."""
        self.drop_streams()
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def __aenter__(self) -> Self:
        """Start answering."""
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        """Stop answering."""
        await self.close()

    # ------------------------------------------------------------------- the stream

    @property
    def streams(self) -> int:
        """How many event streams are open."""
        return len(self._streams)

    async def wait_for_stream(self, timeout: float = 5.0) -> None:
        """Wait until an event stream has been opened since the last call."""
        await asyncio.wait_for(self._stream_opened.wait(), timeout)
        self._stream_opened.clear()

    def emit(self, kind: str, shade_id: int, **fields: object) -> None:
        """Send an event about a shade to every open stream."""
        shade = self.shades.get(shade_id)
        event: dict[str, object] = {
            "evt": kind,
            "isoDate": _iso_now(),
            "bleName": shade.ble_name if shade else f"SIM:{shade_id:04d}",
            "id": shade_id,
        }
        if shade is not None:
            event["currentPositions"] = dict(shade.positions)
        event.update(fields)
        line = f"data: {json.dumps(event)}\n\n"
        for queue in self._streams:
            queue.put_nowait(line)

    def drop_streams(self) -> None:
        """End every open event stream, as the gateway does when it restarts."""
        for queue in self._streams:
            queue.put_nowait(None)

    def break_streams(self) -> None:
        """Cut every open event stream off in the middle of a line, as a broken connection does."""
        for queue in self._streams:
            queue.put_nowait(_BREAK)

    # ------------------------------------------------------------------ the shades

    def move(self, shade_id: int, positions: Mapping[Rail, float]) -> None:
        """Move a shade part of the way; the gateway sends no event for it."""
        self.shades[shade_id].positions.update(positions)

    def finish(self, shade_id: int) -> None:
        """End a shade's move at its target, and send motion-stopped."""
        shade = self.shades[shade_id]
        if shade.target is not None:
            shade.positions.update(shade.target)
            shade.target = None
        self.emit(EventKind.MOTION_STOPPED, shade_id)

    def set_online(self, shade_id: int, online: bool) -> None:
        """Report a shade coming online or going offline."""
        self.emit(EventKind.SHADE_ONLINE if online else EventKind.SHADE_OFFLINE, shade_id)

    def _start_motion(self, shade: SimulatedShade, positions: Mapping[Rail, float]) -> None:
        announced = dict(shade.target or shade.positions)
        announced.update(positions)
        target = dict(announced)
        if shade.capabilities == Capabilities.TOP_DOWN_BOTTOM_UP:
            # The shade's own firmware stops a rail where it meets the other one; the gateway
            # announces the target it was sent.
            moved = Rail.SECONDARY if Rail.SECONDARY in positions else Rail.PRIMARY
            other = Rail.PRIMARY if moved is Rail.SECONDARY else Rail.SECONDARY
            target[moved] = min(target[moved], round(1.0 - target[other], 4))
        shade.target = target
        distance = max(abs(announced[rail] - shade.positions[rail]) for rail in RAILS)
        self.emit(EventKind.BATTERY_ALERT, shade.id, batteryLevel=1)
        self.emit(
            EventKind.MOTION_STARTED, shade.id,
            targetPositions={**announced, "etaInSeconds": round(distance * _ETA_PER_FULL_MOVE)},
        )

    # ------------------------------------------------------------------- handlers

    async def _record(self, request: web.Request) -> object:
        body: object = None
        if request.can_read_body:
            body = cast(object, await request.json())
        self.requests.append(Request(request.method, request.path, dict(request.query), body))
        return body

    def _shade_ids(self, request: web.Request) -> list[SimulatedShade]:
        try:
            ids = [int(part) for part in request.query.get("ids", "").split(",")]
            return [self.shades[shade_id] for shade_id in ids]
        except (KeyError, ValueError) as err:
            raise web.HTTPBadRequest(text=f"unknown shade ids: {request.query.get('ids')}") from err

    def _answer(self, shades: list[SimulatedShade]) -> web.Response:
        return web.json_response({
            "err": 0,
            "responses": [
                {"bleName": shade.ble_name, "err": self.command_errors.get(shade.id, 0),
                 "id": shade.id, "hex": "00"}
                for shade in shades
            ],
            "wentViaHTTP": True,
        })

    async def _home(self, request: web.Request) -> web.Response:
        await self._record(request)
        return web.json_response(self.home())

    async def _gateway(self, request: web.Request) -> web.Response:
        await self._record(request)
        return web.json_response(self.gateway())

    async def _all_shades(self, request: web.Request) -> web.Response:
        await self._record(request)
        return web.json_response([shade.document() for shade in self.shades.values()])

    async def _shade(self, request: web.Request) -> web.Response:
        await self._record(request)
        await asyncio.sleep(self.shade_read_delay)
        shade = self.shades.get(int(request.match_info["id"]))
        if shade is None:
            raise web.HTTPNotFound()
        return web.json_response(shade.document())

    async def _positions(self, request: web.Request) -> web.Response:
        body = await self._record(request)
        shades = self._shade_ids(request)
        raw = cast(dict[str, object], body).get("positions") if isinstance(body, dict) else None
        if not isinstance(raw, dict):
            raise web.HTTPBadRequest(text="no positions")
        given = cast(dict[str, object], raw)
        positions = {
            rail: float(value) for rail in RAILS
            if isinstance(value := given.get(rail), int | float)
        }
        for shade in shades:
            if shade.id not in self.command_errors:
                self._start_motion(shade, positions)
        return self._answer(shades)

    async def _stop(self, request: web.Request) -> web.Response:
        await self._record(request)
        shades = self._shade_ids(request)
        for shade in shades:
            shade.target = None
            self.emit(EventKind.MOTION_STOPPED, shade.id)
        return self._answer(shades)

    async def _motion(self, request: web.Request) -> web.Response:
        body = await self._record(request)
        shade = self.shades.get(int(request.match_info["id"]))
        motion = cast(dict[str, object], body).get("motion") if isinstance(body, dict) else None
        if shade is None:
            raise web.HTTPNotFound()
        if motion != "jog":
            return web.json_response(
                {"errMsg": 'motion property must be one of ["jog"]'}, status=400)
        return self._answer([shade])

    async def _activate(self, request: web.Request) -> web.Response:
        await self._record(request)
        scene = self.scenes.get(int(request.match_info["id"]))
        if scene is None:
            raise web.HTTPNotFound()
        for shade_id, positions in scene.members.items():
            if (shade := self.shades.get(shade_id)) is not None:
                self._start_motion(shade, positions)
        return web.json_response({"shadeIds": list(scene.members)})

    async def _events(self, request: web.Request) -> web.StreamResponse:
        await self._record(request)
        if self.refuse_streams:
            return web.Response(status=503)
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._streams.append(queue)
        self._stream_opened.set()
        try:
            # A client may go between two writes.
            with contextlib.suppress(ConnectionError):
                await response.write(b"100 HELO\n\n")
                while (line := await queue.get()) is not None:
                    if line is _BREAK:
                        await response.write(b"data: {\"evt\": \"motion-sta")
                        # The stream's connection is open while it is written to.
                        cast(asyncio.BaseTransport, request.transport).close()
                        break
                    await response.write(line.encode())
        finally:
            self._streams.remove(queue)
        return response
