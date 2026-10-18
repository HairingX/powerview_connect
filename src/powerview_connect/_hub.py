"""A whole home, kept current from its primary gateway's event stream."""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import TracebackType
from typing import Self

import aiohttp

from ._client import (
    DEFAULT_TIMEOUT,
    CannotConnectError,
    PowerViewClient,
    PowerViewError,
    ReadOnlyError,
    StreamIdleError,
)
from ._clock import Clock, SystemClock
from ._covers import CoverRole, CoverSpec, covers
from ._model import (
    EventKind,
    Gateway,
    GatewayStatus,
    Home,
    Rail,
    Shade,
    ShadeEvent,
)
from ._motion import STEP, ShadeTracker
from ._retry import RetryOutcome, StalledOpenRetry, retry_steps, stalled_openness

type Listener[T] = Callable[[T], None]
type Unsubscribe = Callable[[], None]


@dataclass(frozen=True, slots=True, kw_only=True)
class Timing:
    """How often a hub asks the gateways, and how long things may take, in seconds."""

    poll_interval: float = 1.0
    """Between two reads of a moving shade: it reports a new position about once a second."""
    refresh_interval: float = 600.0
    """Between two reads of the whole home, in case an event was missed."""
    gateway_interval: float = 60.0
    """Between two questions to each gateway of its own state."""
    request_timeout: float = DEFAULT_TIMEOUT
    """Longest a request may take before the gateway counts as not answering."""
    stream_idle_timeout: float = 360.0
    """Silence after which the event stream is reopened, to catch one broken without closing."""
    reconnect_backoff: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
    """Waits before each new attempt at the event stream, the last one repeated."""
    stream_healthy_after: float = 30.0
    """How long a stream must stay open, without an event, before the waits start over."""
    max_concurrent_reads: int = 3
    """Reads of moving shades sent to the gateway at once."""
    removal_confirmations: int = 2
    """Reads of the home in a row that must miss a shade, scene or gateway before it is gone."""
    stall: float = 6.0
    """No change for this long, after a moving shade changed, ends its move."""
    settle: float = 1.5
    """A shade still at its target for this long has ended its move."""
    start_grace: float = 25.0
    """Nothing moving this long after a move started ends it: the motor starts some seconds
    after the command."""
    min_timeout: float = 90.0
    """The shortest time after which a move ends anyway, however short the gateway's estimate
    of it."""
    retry_move_timeout: float = 90.0
    """Longest a move of a retried opening may take to stop."""
    retry_settle: float = 3.0
    """How long a shade must have sent no event after a step of a retry stopped before where it
    ended counts: a motor backing off reports a position every 2 s."""

    def __post_init__(self) -> None:
        """Refuse values the hub cannot work with.

        Raises:
            ValueError: an interval or a limit of a move is not positive, `settle` or
                `retry_settle` is negative, no
                wait is given before reopening the stream, or fewer than one read or
                confirmation is allowed.
        """
        intervals = (self.poll_interval, self.refresh_interval, self.gateway_interval,
                     self.request_timeout, self.stream_idle_timeout, self.retry_move_timeout,
                     self.stall, self.start_grace, self.min_timeout)
        if any(interval <= 0 for interval in intervals) or min(self.settle, self.retry_settle) < 0:
            raise ValueError(f"intervals and timeouts must be positive, settles 0 or more: {self}")
        if not self.reconnect_backoff or any(wait < 0 for wait in self.reconnect_backoff):
            raise ValueError(f"reconnect_backoff needs waits of 0 or more: {self}")
        if self.max_concurrent_reads < 1 or self.removal_confirmations < 1:
            raise ValueError(f"max_concurrent_reads and removal_confirmations start at 1: {self}")


class Connection(StrEnum):
    """How the hub reaches the primary gateway."""

    CONNECTING = "connecting"
    """The home has not been read yet."""
    CONNECTED = "connected"
    """The gateway answers and the event stream is open."""
    STREAM_DOWN = "stream_down"
    """The gateway answers, and the event stream is being reopened."""
    UNREACHABLE = "unreachable"
    """The gateway does not answer."""


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadeState:
    """A shade as the hub knows it now."""

    shade: Shade
    """The shade as last read: its name, room, capabilities, signal strength and battery."""
    positions: Mapping[Rail, float]
    """Each rail's position now, 0.0 closed to 1.0 open; a rail not known is absent."""
    targets: Mapping[Rail, float]
    """Each rail's target while the shade moves."""
    directions: Mapping[Rail, int]
    """1 for a rail opening, -1 for one closing; absent for one standing still."""
    moving: bool
    online: bool
    eta: float | None
    """The gateway's estimate of the move's time in seconds; real moves take about twice as long."""

    @property
    def id(self) -> int:
        """The shade's id."""
        return self.shade.id

    @property
    def covers(self) -> tuple[CoverSpec, ...]:
        """The covers the shade is controlled as."""
        return covers(self.shade)


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayState:
    """A gateway of the home as the hub knows it now."""

    gateway: Gateway
    """The gateway as the home lists it."""
    askable: bool
    """The gateway has an address the hub can ask it at."""
    reachable: bool | None
    """Whether it answered when last asked; None before it was asked."""
    status: GatewayStatus | None
    """What it last reported of itself; kept while it does not answer."""


@dataclass(frozen=True, slots=True, kw_only=True)
class HomeDiff:
    """What changed in the home between two reads, besides positions."""

    added_shades: frozenset[int] = frozenset()
    removed_shades: frozenset[int] = frozenset()
    changed_shades: frozenset[int] = frozenset()
    """Shades with another name, room, gateway, capabilities or firmware."""
    added_scenes: frozenset[int] = frozenset()
    removed_scenes: frozenset[int] = frozenset()
    changed_scenes: frozenset[int] = frozenset()
    added_gateways: frozenset[str] = frozenset()
    removed_gateways: frozenset[str] = frozenset()
    changed_gateways: frozenset[str] = frozenset()

    def __bool__(self) -> bool:
        """Whether anything changed."""
        return any(getattr(self, found.name) for found in dataclasses.fields(self))


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadeChanged:
    """A shade's state changed."""

    state: ShadeState


@dataclass(frozen=True, slots=True, kw_only=True)
class GatewayChanged:
    """A gateway's state changed."""

    state: GatewayState


@dataclass(frozen=True, slots=True, kw_only=True)
class ConnectionChanged:
    """The connection to the primary gateway changed."""

    connection: Connection


@dataclass(frozen=True, slots=True, kw_only=True)
class HomeChanged:
    """Shades, scenes or gateways were added, removed or changed."""

    home: Home
    diff: HomeDiff


@dataclass(frozen=True, slots=True, kw_only=True)
class ShadeStalled:
    """A cover's opening stalled short of fully open, and the hub retries it.

    Sent once as the retry starts, with where the cover stopped, and once as it ends, with where
    the cover ended.
    """

    shade_id: int
    role: CoverRole
    openness: float | None
    """How far the cover is open, 0.0 closed to 1.0 open; None where it is not known."""
    outcome: RetryOutcome


type Change = ShadeChanged | GatewayChanged | ConnectionChanged | HomeChanged | ShadeStalled


class _Interrupted(Exception):
    """A retry was interrupted by something else moving, stopping or removing its shade."""


class _GaveUp(Exception):
    """A retry stopped trying, for the reason given."""


@dataclass(slots=True)
class _ActiveRetry:
    cover: CoverSpec
    expected: float | None = None
    """The openness of the move sent, while it is awaited."""
    heard_at: float = 0.0
    """When the shade last sent an event, on the hub's clock."""
    interrupted: bool = False
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    """Set by any motion-stopped of the shade, and by an interruption."""


def _metadata(shade: Shade) -> Shade:
    return dataclasses.replace(shade, positions={}, signal_strength=None, battery_status=None)


def _diff[K, V](old: Mapping[K, V], new: Mapping[K, V]) -> tuple[frozenset[K], frozenset[K], frozenset[K]]:
    added = frozenset(new.keys() - old.keys())
    removed = frozenset(old.keys() - new.keys())
    changed = frozenset(key for key in old.keys() & new.keys() if old[key] != new[key])
    return added, removed, changed


@dataclass(slots=True)
class _Listeners:
    every: list[Listener[Change]] = field(default_factory=lambda: [])
    shades: dict[int, list[Listener[ShadeState]]] = field(default_factory=lambda: {})
    gateways: dict[str, list[Listener[GatewayState]]] = field(default_factory=lambda: {})


class PowerViewHub:
    """One PowerView home, reached through its primary gateway, kept current while it runs.

    `load` reads the home; `run` then follows it until cancelled: the event stream, reads of
    moving shades, a read of the whole home now and then, and each gateway's own state. Changes
    reach the listeners given to `subscribe`, `subscribe_shade` and `subscribe_gateway`, called
    in the event loop. Commands work once the home is loaded, whether or not `run` runs.

    Use it as an async context manager to load it and run it in a task of its own, or call
    `load` and run `run` as a task the caller owns.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        *,
        timing: Timing | None = None,
        clock: Clock | None = None,
        logger: logging.Logger | None = None,
        read_only: bool = False,
    ) -> None:
        """A hub for the primary gateway at `host`, on a session its owner keeps.

        Args:
            read_only: refuse every command before it reaches the gateway, with `ReadOnlyError`.
        """
        self._timing = timing or Timing()
        self._clock = clock or SystemClock()
        self._log = logger or logging.getLogger("powerview_connect")
        self._session = session
        self._client = PowerViewClient(session, host, timeout=self._timing.request_timeout,
                                       read_only=read_only)
        self._home: Home | None = None
        self._shades: dict[int, Shade] = {}
        self._trackers: dict[int, ShadeTracker] = {}
        self._offline: set[int] = set()
        """The shades the gateway last reported offline."""
        self._states: dict[int, ShadeState] = {}
        self._missing: dict[tuple[str, int | str], int] = {}
        self._gateway_clients: dict[str, PowerViewClient] = {}
        self._gateway_states: dict[str, GatewayState] = {}
        self._moving: set[int] = set()
        self._wake = asyncio.Event()
        self._reads = asyncio.Semaphore(self._timing.max_concurrent_reads)
        self._reachable = False
        self._stream_open = False
        self._connection = Connection.CONNECTING
        self._listeners = _Listeners()
        self._task: asyncio.Task[None] | None = None
        self._retries: dict[tuple[int, CoverRole], StalledOpenRetry] = {}
        """The retry settings of each cover, by shade id and role."""
        self._active: dict[int, _ActiveRetry] = {}
        self._group: asyncio.TaskGroup | None = None

    # ---------------------------------------------------------------- what is known

    @property
    def host(self) -> str:
        """The primary gateway's address, as it is reached, with a port where one was given."""
        return self._client.host

    @property
    def base_url(self) -> str:
        """The URL the primary gateway's API is under, such as `http://192.0.2.32`."""
        return self._client.base_url

    @property
    def read_only(self) -> bool:
        """Whether every command is refused before it reaches the gateway."""
        return self._client.read_only

    @property
    def home(self) -> Home:
        """The home as last read.

        Raises:
            RuntimeError: the home has not been loaded.
        """
        return self._loaded()

    def _loaded(self) -> Home:
        if self._home is None:
            raise RuntimeError("the home has not been loaded")
        return self._home

    @property
    def connection(self) -> Connection:
        """How the hub reaches the primary gateway."""
        return self._connection

    @property
    def available(self) -> bool:
        """Whether the primary gateway answers, so the shades can be shown and moved."""
        return self._connection in (Connection.CONNECTED, Connection.STREAM_DOWN)

    @property
    def gateway(self) -> Gateway | None:
        """The gateway the hub reaches, as the home lists it."""
        return self.home.gateway_at(self._client.host) or self.home.primary_gateway

    @property
    def shades(self) -> Mapping[int, ShadeState]:
        """Every shade's state, by shade id."""
        return dict(self._states)

    def shade(self, shade_id: int) -> ShadeState:
        """A shade's state.

        Raises:
            KeyError: the home has no such shade.
        """
        return self._states[shade_id]

    @property
    def gateways(self) -> Mapping[str, GatewayState]:
        """Every gateway's state, by serial number."""
        return dict(self._gateway_states)

    def stalled_open_retry(self, shade_id: int, role: CoverRole) -> StalledOpenRetry | None:
        """How a stalled opening of the shade's cover `role` is retried; None while it is not."""
        return self._retries.get((shade_id, role))

    def set_stalled_open_retry(
        self, shade_id: int, role: CoverRole, retry: StalledOpenRetry | None
    ) -> None:
        """Retry an opening of the shade's cover `role` that stalls short of fully open, as
        `retry` says; None stops doing so.

        Off for every cover until turned on. It works while `run` runs; new settings apply from
        the next stall, and None ends a retry of the cover in progress.

        Raises:
            KeyError: the home has no such shade.
            ValueError: the shade has no cover `role` whose opening can be retried.
            ReadOnlyError: it is turned on for a read-only hub, which sends no commands.
        """
        if shade_id not in self._trackers:
            raise KeyError(shade_id)
        if retry is None:
            self._retries.pop((shade_id, role), None)
            active = self._active.get(shade_id)
            if active is not None and active.cover.role is role:
                self._interrupt(shade_id)
            return
        if not any(cover.retryable and cover.role is role
                   for cover in covers(self._shades[shade_id])):
            raise ValueError(f"shade {shade_id} has no {role} cover whose opening can be retried")
        if self.read_only:
            raise ReadOnlyError(f"retrying shade {shade_id} refused: the hub is read-only")
        self._retries[(shade_id, role)] = retry

    def gateway_of(self, shade_id: int) -> Gateway | None:
        """The gateway that serves a shade, the one the hub reaches when no gateway says so."""
        return self.home.gateway_of(shade_id) or self.gateway

    def snapshot(self) -> dict[str, object]:
        """What a bug report needs, without addresses, serial numbers or Bluetooth names.

        Its keys and their contents are for a person to read, and may change in any release.
        """
        return {
            "connection": str(self._connection),
            "stream_open": self._stream_open,
            "gateways": [
                {
                    "primary": state.gateway.is_primary, "model": state.gateway.model,
                    "firmware": state.gateway.firmware,
                    "radio_firmware": list(state.gateway.radio_firmware),
                    "shades": list(state.gateway.shade_ids), "askable": state.askable,
                    "reachable": state.reachable,
                    "status": None if state.status is None else {
                        "hardware_version": state.status.hardware_version,
                        "firmware": state.status.firmware,
                        "primary": state.status.is_primary,
                        "interface": state.status.interface,
                        "internet_state": state.status.internet_state,
                    },
                }
                for state in self._gateway_states.values()
            ],
            "shades": {
                str(shade_id): {
                    "capabilities": state.shade.capabilities, "type": state.shade.type,
                    "power_type": state.shade.power_type, "firmware": state.shade.firmware,
                    "room": state.shade.room_name,
                    "signal_strength": state.shade.signal_strength,
                    "battery_status": state.shade.battery_status,
                    "positions": {str(rail): value for rail, value in state.positions.items()},
                    "targets": {str(rail): value for rail, value in state.targets.items()},
                    "moving": state.moving, "online": state.online,
                }
                for shade_id, state in self._states.items()
            },
            "scenes": len(self.home.scenes),
        }

    # ------------------------------------------------------------------ listening

    def subscribe(self, listener: Listener[Change]) -> Unsubscribe:
        """Call `listener` with every change; returns what stops it."""
        self._listeners.every.append(listener)
        return lambda: self._listeners.every.remove(listener)

    def subscribe_shade(self, shade_id: int, listener: Listener[ShadeState]) -> Unsubscribe:
        """Call `listener` with a shade's state each time it changes; returns what stops it."""
        listeners = self._listeners.shades.setdefault(shade_id, [])
        listeners.append(listener)
        return lambda: listeners.remove(listener)

    def subscribe_gateway(self, serial: str, listener: Listener[GatewayState]) -> Unsubscribe:
        """Call `listener` with a gateway's state each time it changes; returns what stops it."""
        listeners = self._listeners.gateways.setdefault(serial, [])
        listeners.append(listener)
        return lambda: listeners.remove(listener)

    async def changes(self) -> AsyncIterator[Change]:
        """Every change from now on, for as long as the iteration goes on.

        The changes queue up until taken, so a consumer must keep taking them.
        """
        queue: asyncio.Queue[Change] = asyncio.Queue()
        unsubscribe = self.subscribe(queue.put_nowait)
        try:
            while True:
                yield await queue.get()
        finally:
            unsubscribe()

    def _call[T](self, listeners: list[Listener[T]], value: T) -> None:
        for listener in list(listeners):
            try:
                listener(value)
            except Exception:
                self._log.exception("A PowerView listener failed")

    def _emit(self, change: Change) -> None:
        self._call(self._listeners.every, change)
        match change:
            case ShadeChanged(state=state):
                self._call(self._listeners.shades.get(state.id, []), state)
            case GatewayChanged(state=state):
                self._call(self._listeners.gateways.get(state.gateway.serial, []), state)
            case _:
                pass

    def _emit_stall(self, shade_id: int, cover: CoverSpec, openness: float | None,
                    outcome: RetryOutcome) -> None:
        self._emit(ShadeStalled(shade_id=shade_id, role=cover.role, openness=openness,
                                outcome=outcome))

    # -------------------------------------------------------------------- state

    def _state_of(self, shade_id: int) -> ShadeState:
        tracker = self._trackers[shade_id]
        return ShadeState(
            shade=self._shades[shade_id],
            positions={rail: value for rail, value in tracker.current.items() if value is not None},
            targets={rail: value for rail, value in tracker.target.items()
                     if value is not None and tracker.moving},
            directions={rail: direction for rail in tracker.current
                        if (direction := tracker.direction(rail))},
            moving=tracker.moving,
            online=shade_id not in self._offline,
            eta=tracker.eta,
        )

    def _update_shade(self, shade_id: int) -> None:
        state = self._state_of(shade_id)
        if state != self._states.get(shade_id):
            self._states[shade_id] = state
            self._emit(ShadeChanged(state=state))

    def _set_connection(self) -> None:
        if not self._reachable:
            connection = Connection.UNREACHABLE
        else:
            connection = Connection.CONNECTED if self._stream_open else Connection.STREAM_DOWN
        if connection == self._connection:
            return
        was_available = self.available
        self._connection = connection
        if was_available and not self.available:
            self._log.warning("The PowerView gateway at %s does not answer", self._client.host)
        elif self.available and not was_available and self._home is not None:
            self._log.info("The PowerView gateway at %s answers", self._client.host)
        self._emit(ConnectionChanged(connection=connection))

    def _set_reachable(self, reachable: bool) -> None:
        self._reachable = reachable
        self._set_connection()

    def _set_stream(self, stream_open: bool) -> None:
        self._stream_open = stream_open
        self._set_connection()

    def _new_tracker(self, shade: Shade) -> ShadeTracker:
        timing = self._timing
        return ShadeTracker(shade.positions, stall=timing.stall, settle=timing.settle,
                            start_grace=timing.start_grace, min_timeout=timing.min_timeout)

    def _set_gateways(self, home: Home) -> set[str]:
        """Follow the gateways of `home`, with a client for each; returns those whose state
        changed."""
        reached = home.gateway_at(self._client.host) or home.primary_gateway
        changed: set[str] = set()
        for gateway in home.gateways:
            old = self._gateway_states.get(gateway.serial)
            if gateway == reached:
                self._gateway_clients[gateway.serial] = self._client
            elif gateway.ip and (old is None or old.gateway.ip != gateway.ip):
                # Only ever asked of its state, so nothing can reach a shade through it.
                self._gateway_clients[gateway.serial] = PowerViewClient(
                    self._session, gateway.ip, timeout=self._timing.request_timeout,
                    read_only=True)
            elif not gateway.ip:
                self._gateway_clients.pop(gateway.serial, None)
            keep = old is not None and old.gateway.ip == gateway.ip
            state = GatewayState(
                gateway=gateway, askable=gateway.serial in self._gateway_clients,
                reachable=old.reachable if keep and old else None,
                status=old.status if old else None,
            )
            if state != old:
                self._gateway_states[gateway.serial] = state
                changed.add(gateway.serial)
        for serial in self._gateway_states.keys() - {gateway.serial for gateway in home.gateways}:
            del self._gateway_states[serial]
            self._gateway_clients.pop(serial, None)
        return changed

    # ------------------------------------------------------------------- reading

    async def _request[T](self, what: str, request: Awaitable[T]) -> T:
        started = self._clock.monotonic()
        try:
            found = await request
        except ReadOnlyError:
            raise
        except PowerViewError as err:
            self._log.debug("%s failed after %.2f s", what, self._clock.monotonic() - started)
            # Any answer, an error included, shows the gateway answers.
            self._set_reachable(not isinstance(err, CannotConnectError))
            raise
        self._log.debug("%s took %.2f s", what, self._clock.monotonic() - started)
        self._set_reachable(True)
        return found

    async def load(self) -> None:
        """Read the home; the gateways are first asked of their state once `run` runs.

        Raises:
            CannotConnectError: the gateway does not answer.
            ResponseError: it answered with something other than a home.
        """
        home = await self._request("Reading the home", self._client.get_home())
        if self._home is not None:
            self._apply(home)
            return
        self._home = home
        for shade in home.shades.values():
            self._shades[shade.id] = shade
            self._trackers[shade.id] = self._new_tracker(shade)
            self._states[shade.id] = self._state_of(shade.id)
        self._set_gateways(home)
        self._set_connection()

    async def refresh(self) -> None:
        """Read the whole home now: positions of shades standing still, and what changed.

        Raises:
            RuntimeError: the home has not been loaded.
            CannotConnectError: the gateway does not answer.
            ResponseError: it answered with something other than a home.
        """
        self._loaded()
        self._apply(await self._request("Reading the home", self._client.get_home()))

    def _merge(self, new: Home) -> Home:
        """`new`, with what it misses kept until `removal_confirmations` reads in a row miss it."""
        old = self._loaded()
        shades = dict(new.shades)
        for shade_id, shade in old.shades.items():
            if shade_id not in shades and self._confirm_missing(("shade", shade_id)):
                shades[shade_id] = shade
        scenes = {scene.id: scene for scene in new.scenes}
        for scene in old.scenes:
            if scene.id not in scenes and self._confirm_missing(("scene", scene.id)):
                scenes[scene.id] = scene
        gateways = {gateway.serial: gateway for gateway in new.gateways}
        for gateway in old.gateways:
            if gateway.serial not in gateways and self._confirm_missing(("gateway", gateway.serial)):
                gateways[gateway.serial] = gateway
        present = ({("shade", key) for key in new.shades}
                   | {("scene", scene.id) for scene in new.scenes}
                   | {("gateway", gateway.serial) for gateway in new.gateways})
        for key in present & self._missing.keys():
            del self._missing[key]
        return Home(home_id=new.home_id, gateways=tuple(gateways.values()), shades=shades,
                    scenes=tuple(scenes.values()))

    def _confirm_missing(self, key: tuple[str, int | str]) -> bool:
        """Count one more read missing `key`; returns whether it is still kept."""
        count = self._missing.get(key, 0) + 1
        if count < self._timing.removal_confirmations:
            self._missing[key] = count
            return True
        self._missing.pop(key, None)
        return False

    def _apply(self, read: Home) -> None:
        old = self._loaded()
        home = self._merge(read)
        added, removed, changed = _diff(
            {key: _metadata(shade) for key, shade in old.shades.items()},
            {key: _metadata(shade) for key, shade in home.shades.items()},
        )
        moved = frozenset(
            shade_id for shade_id in home.shades.keys() & old.shades.keys()
            if _serial(home.gateway_of(shade_id)) != _serial(old.gateway_of(shade_id)))
        scenes_added, scenes_removed, scenes_changed = _diff(
            {scene.id: scene for scene in old.scenes}, {scene.id: scene for scene in home.scenes})
        gateways_added, gateways_removed, gateways_changed = _diff(
            {gateway.serial: _gateway_metadata(gateway) for gateway in old.gateways},
            {gateway.serial: _gateway_metadata(gateway) for gateway in home.gateways})
        diff = HomeDiff(
            added_shades=added, removed_shades=removed, changed_shades=changed | moved,
            added_scenes=scenes_added, removed_scenes=scenes_removed,
            changed_scenes=scenes_changed, added_gateways=gateways_added,
            removed_gateways=gateways_removed, changed_gateways=gateways_changed)
        self._home = home
        for shade_id in removed:
            for key in [key for key in self._retries if key[0] == shade_id]:
                del self._retries[key]
            self._interrupt(shade_id)
            del self._shades[shade_id], self._trackers[shade_id], self._states[shade_id]
            self._moving.discard(shade_id)
            self._offline.discard(shade_id)
        for shade_id, shade in home.shades.items():
            if shade_id in added:
                self._trackers[shade_id] = self._new_tracker(shade)
            else:
                self._trackers[shade_id].snapshot(shade.positions)
            self._shades[shade_id] = shade
        # Every state is current before any listener is told, so each reads the home as it is.
        changed_states: list[ShadeState] = []
        for shade_id in home.shades:
            state = self._state_of(shade_id)
            if state != self._states.get(shade_id):
                self._states[shade_id] = state
                if shade_id not in added:
                    changed_states.append(state)
        for serial in self._set_gateways(home):
            self._emit(GatewayChanged(state=self._gateway_states[serial]))
        if diff:
            self._emit(HomeChanged(home=home, diff=diff))
        for state in changed_states:
            self._emit(ShadeChanged(state=state))

    async def refresh_gateways(self) -> None:
        """Ask every gateway that can be asked of its own state, all at once.

        A gateway that does not answer is kept as not reachable, with its last known status.
        """
        serials = list(self._gateway_clients)
        found = await asyncio.gather(
            *(self._gateway_clients[serial].get_gateway() for serial in serials),
            return_exceptions=True)
        unexpected: BaseException | None = None
        for serial, answer in zip(serials, found, strict=True):
            old = self._gateway_states.get(serial)
            if old is None:
                continue
            if isinstance(answer, BaseException):
                if not isinstance(answer, PowerViewError):
                    unexpected = unexpected or answer
                    continue
                self._log.debug("Gateway %s does not answer: %s", old.gateway.name, answer)
                state = dataclasses.replace(old, reachable=False)
            else:
                state = dataclasses.replace(old, reachable=True, status=answer)
            if state != old:
                self._gateway_states[serial] = state
                self._emit(GatewayChanged(state=state))
        if unexpected is not None:
            raise unexpected

    # ------------------------------------------------------------------ running

    async def run(self) -> None:
        """Follow the home until cancelled; it never returns on its own.

        The gateway not answering is not an error: the connection says so, and the hub keeps
        trying. An unexpected error in one of its loops is logged, and that loop goes on.

        Raises:
            RuntimeError: the home has not been loaded.
        """
        self._loaded()
        try:
            async with asyncio.TaskGroup() as group:
                self._group = group
                group.create_task(self._follow_stream())
                group.create_task(self._poll_moving())
                group.create_task(self._every(self._timing.refresh_interval, self._refresh_quietly,
                                              first=False))
                group.create_task(self._every(self._timing.gateway_interval, self.refresh_gateways,
                                              first=True))
        finally:
            self._group = None
            self._set_stream(False)

    async def _every(
        self, interval: float, step: Callable[[], Awaitable[None]], *, first: bool
    ) -> None:
        if not first:
            await self._clock.sleep(interval)
        while True:
            try:
                await step()
            except Exception:
                self._log.exception("A PowerView hub task failed; it goes on")
            await self._clock.sleep(interval)

    async def _refresh_quietly(self) -> None:
        try:
            await self.refresh()
        except PowerViewError as err:
            self._log.debug("Reading the home failed: %s", err)

    async def _follow_stream(self) -> None:
        attempt = 0
        while True:
            opened_at: float | None = None
            healthy = idle = False
            try:
                async with self._client.shade_events(self._timing.stream_idle_timeout) as events:
                    opened_at = self._clock.monotonic()
                    self._set_stream(True)
                    self._log.debug("Listening to the events of %s", self._client.host)
                    # What happened while the stream was closed is read once it is open.
                    await self._refresh_quietly()
                    async for event in events:
                        healthy = True
                        self._handle(event)
                self._log.debug("The gateway at %s closed its event stream", self._client.host)
            except StreamIdleError:
                self._log.debug("The event stream of %s was silent; reopening it", self._client.host)
                idle = True
            except CannotConnectError as err:
                self._log.debug("The event stream of %s: %s", self._client.host, err)
                if opened_at is None:
                    self._set_reachable(False)
            except PowerViewError as err:
                self._log.debug("The event stream of %s: %s", self._client.host, err)
            except Exception:
                self._log.exception("The PowerView event stream failed; it is reopened")
            self._set_stream(False)
            if idle:
                # Silence is how the stream looks while nothing moves: reopen it at once.
                attempt = 0
                continue
            if opened_at is not None and (
                healthy or self._clock.monotonic() - opened_at >= self._timing.stream_healthy_after
            ):
                attempt = 0
            backoff = self._timing.reconnect_backoff
            await self._clock.sleep(backoff[min(attempt, len(backoff) - 1)])
            attempt += 1

    def _handle(self, event: ShadeEvent) -> None:
        self._log.debug("Event: %s", event)
        tracker = self._trackers.get(event.shade_id)
        if tracker is None:
            return
        active = self._active.get(event.shade_id)
        if active is not None:
            active.heard_at = self._clock.monotonic()
        match event.kind:
            case EventKind.MOTION_STARTED:
                tracker.motion_started(event, self._clock.monotonic())
                self._poll(event.shade_id)
                if active is not None:
                    aimed = active.cover.openness(event.target)
                    # A move without a target for the rail cannot be told from the retry's own.
                    if aimed is not None and (active.expected is None
                                              or abs(aimed - active.expected) > STEP):
                        self._interrupt(event.shade_id)
            case EventKind.MOTION_STOPPED:
                tracker.motion_stopped(event)
                self._moving.discard(event.shade_id)
            case EventKind.SHADE_ONLINE:
                self._offline.discard(event.shade_id)
                # A motor backing off by itself reports where the shade is in these, and in no
                # motion-started.
                tracker.snapshot(event.current)
            case EventKind.SHADE_OFFLINE:
                self._offline.add(event.shade_id)
                self._interrupt(event.shade_id)
            case _:
                # A battery alert comes before some moves, from hard-wired shades too.
                return
        self._update_shade(event.shade_id)
        if event.kind == EventKind.MOTION_STOPPED:
            if active is None:
                self._detect_stall(event.shade_id)
            else:
                active.wake.set()

    # ----------------------------------------------------------------- stalls

    def _detect_stall(self, shade_id: int) -> None:
        group = self._group
        if group is None or not any(key[0] == shade_id for key in self._retries):
            return
        tracker = self._trackers[shade_id]
        final = {rail: value for rail, value in tracker.current.items() if value is not None}
        for cover in covers(self._shades[shade_id]):
            settings = self._retries.get((shade_id, cover.role))
            if settings is None:
                continue
            openness = stalled_openness(cover, tracker.last_target, final, settings)
            if openness is None:
                continue
            # One retry per stalled move: its own moves' stops are not looked at again.
            tracker.last_target = {}
            active = _ActiveRetry(cover, heard_at=self._clock.monotonic())
            self._active[shade_id] = active
            self._log.warning("Shade %s (%s) stopped at %.2f of fully open; retrying its %s "
                              "cover", shade_id, self._name(shade_id), openness, cover.role)
            self._emit_stall(shade_id, cover, openness, RetryOutcome.STARTED)
            group.create_task(self._retry(shade_id, active, openness, settings))
            return

    def _interrupt(self, shade_id: int) -> None:
        active = self._active.get(shade_id)
        if active is not None:
            active.interrupted = True
            active.wake.set()

    def _cover_openness(self, shade_id: int, cover: CoverSpec) -> float | None:
        tracker = self._trackers.get(shade_id)
        if tracker is None:
            return None
        return cover.openness({rail: value for rail, value in tracker.current.items()
                               if value is not None})

    def _name(self, shade_id: int) -> str:
        return self._shades[shade_id].name if shade_id in self._shades else ""

    async def _retry(self, shade_id: int, active: _ActiveRetry, start: float,
                     settings: StalledOpenRetry) -> None:
        outcome = RetryOutcome.GAVE_UP
        try:
            outcome = await self._raise_in_steps(shade_id, active, start, settings)
        except _Interrupted:
            outcome = RetryOutcome.INTERRUPTED
        except asyncio.CancelledError:
            outcome = RetryOutcome.INTERRUPTED
            raise
        except (_GaveUp, PowerViewError) as err:
            self._log.warning("Retrying shade %s (%s) gave up: %s", shade_id,
                              self._name(shade_id), err)
        except Exception:
            self._log.exception("Retrying shade %s failed", shade_id)
        finally:
            del self._active[shade_id]
            if (tracker := self._trackers.get(shade_id)) is not None:
                tracker.last_target = {}
            openness = self._cover_openness(shade_id, active.cover)
            self._log.warning("Shade %s (%s) ended at %s: %s", shade_id, self._name(shade_id),
                              "unknown" if openness is None else f"{openness:.2f}", outcome)
            self._emit_stall(shade_id, active.cover, openness, outcome)

    async def _raise_in_steps(self, shade_id: int, active: _ActiveRetry, start: float,
                              settings: StalledOpenRetry) -> RetryOutcome:
        name = self._name(shade_id)
        reached = start
        for round_ in range(1, settings.rounds + 1):
            # A motor that stopped short can still be backing off.
            await self._wait_quiet(active, self._timing.retry_settle)
            now = self._cover_openness(shade_id, active.cover)
            reached = reached if now is None else now
            self._log.warning("Shade %s (%s) backed off to %.2f: round %d of %d, lowering it to "
                              "%.2f", shade_id, name, reached, round_, settings.rounds,
                              settings.lower_to)
            # The steps go up from where the motor backed off to, not from where it is lowered.
            steps = retry_steps(reached)
            if reached > settings.lower_to + STEP:
                reached = await self._step(shade_id, active, settings.lower_to)
            for number, openness in enumerate(steps, start=1):
                self._log.warning("Shade %s (%s) at %.2f: step %d of %d to %.2f", shade_id,
                                  name, reached, number, len(steps), openness)
                reached = await self._step(shade_id, active, openness)
                if reached < openness - STEP:
                    self._log.warning("Shade %s (%s) backed off from step %d of %d to %.2f",
                                      shade_id, name, number, len(steps), reached)
                    break
                if number < len(steps):
                    self._log.warning("Shade %s (%s) held at %.2f; next step in %.0f s",
                                      shade_id, name, reached, settings.delay)
                    await self._pause(active, settings.delay)
            else:
                return RetryOutcome.OPENED
        raise _GaveUp(f"backed off in each of {settings.rounds} rounds")

    async def _step(self, shade_id: int, active: _ActiveRetry, openness: float) -> float:
        """Move the cover to `openness`, wait for its stop and for any backing off to end;
        returns how far it is open then.

        Raises:
            _GaveUp: the move did not stop in time, or where the cover is is not known.
        """
        active.wake.clear()
        # Set before sending: the move's motion-started can arrive before the answer.
        active.expected = round(openness, 2)
        await self._send_move(shade_id, active.cover.position(active.expected))
        stopped = await self._wait(active, self._timing.retry_move_timeout)
        active.expected = None
        if stopped:
            # A motor backing off sends a position every 2 s, and a stop at the end.
            await self._wait_quiet(active, self._timing.retry_settle)
        now = self._cover_openness(shade_id, active.cover) if stopped else None
        if now is None:
            raise _GaveUp(f"no stop within {self._timing.retry_move_timeout:.0f} s, or no "
                          "position")
        return now

    async def _pause(self, active: _ActiveRetry, seconds: float) -> None:
        """Wait `seconds` on the hub's clock, whatever the shade sends meanwhile; raises
        `_Interrupted` once the retry is interrupted."""
        until = self._clock.monotonic() + seconds
        while (left := until - self._clock.monotonic()) > 0:
            await self._wait(active, left)
            active.wake.clear()

    async def _wait_quiet(self, active: _ActiveRetry, seconds: float) -> None:
        """Wait on the hub's clock until the shade has sent no event for `seconds`; raises
        `_Interrupted` once the retry is interrupted."""
        while (left := active.heard_at + seconds - self._clock.monotonic()) > 0:
            await self._wait(active, left)
            active.wake.clear()

    async def _wait(self, active: _ActiveRetry, seconds: float) -> bool:
        """Wait on the hub's clock until the retry is woken or `seconds` have passed; returns
        whether it was woken, and raises `_Interrupted` once it is interrupted."""
        woken = asyncio.ensure_future(active.wake.wait())
        slept = asyncio.ensure_future(self._clock.sleep(seconds))
        try:
            await asyncio.wait((woken, slept), return_when=asyncio.FIRST_COMPLETED)
        finally:
            woken.cancel()
            slept.cancel()
        if active.interrupted:
            raise _Interrupted
        return active.wake.is_set()

    def _poll(self, shade_id: int) -> None:
        self._moving.add(shade_id)
        self._wake.set()

    async def _poll_moving(self) -> None:
        while True:
            await self._wake.wait()
            await self._clock.sleep(self._timing.poll_interval)
            moving = sorted(self._moving)
            try:
                await asyncio.gather(*(self._read_moving(shade_id) for shade_id in moving))
            except Exception:
                self._log.exception("Reading the moving shades failed; it goes on")
            if not self._moving:
                self._wake.clear()

    async def _read_moving(self, shade_id: int) -> None:
        async with self._reads:
            # It may have stopped, or left the home, while it waited for its turn.
            if shade_id not in self._moving:
                return
            tracker = self._trackers[shade_id]
            try:
                shade = await self._request(f"Reading shade {shade_id}",
                                            self._client.get_shade(shade_id))
            except PowerViewError as err:
                self._log.debug("Reading shade %s failed: %s", shade_id, err)
                # The timeouts still end a move the shade can no longer be asked about.
                moving = tracker.poll({}, self._clock.monotonic())
            else:
                moving = tracker.poll(shade.positions, self._clock.monotonic())
                if shade_id in self._shades:
                    self._shades[shade_id] = dataclasses.replace(
                        self._shades[shade_id], signal_strength=shade.signal_strength,
                        battery_status=shade.battery_status)
        if not moving:
            self._moving.discard(shade_id)
        if shade_id in self._trackers:
            self._update_shade(shade_id)

    async def __aenter__(self) -> Self:
        """Load the home and follow it in a task of the hub's own."""
        await self.load()
        self._task = asyncio.create_task(self.run(), name=f"PowerView hub {self._client.host}")
        return self

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Stop following the home."""
        await self.close()

    async def close(self) -> None:
        """Stop the task the hub runs in as an async context manager, and wait for it."""
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            # The task's own cancellation is its result; one of the caller's still propagates.
            await asyncio.gather(task, return_exceptions=True)

    # ----------------------------------------------------------------- commands

    async def move(self, shade_id: int, positions: Mapping[Rail, float]) -> None:
        """Move only the rails in `positions`; the shade keeps the others where they are.

        Raises:
            KeyError: the home has no such shade.
            ValueError: no rail is given, or a value is outside 0.0-1.0.
            CommandError: the gateway reported an error for the shade.
            CannotConnectError: the gateway does not answer.
        """
        if shade_id not in self._trackers:
            raise KeyError(shade_id)
        self._interrupt(shade_id)
        await self._send_move(shade_id, positions)

    async def _send_move(self, shade_id: int, positions: Mapping[Rail, float]) -> None:
        tracker = self._trackers[shade_id]
        await self._request(
            f"Moving shade {shade_id} to {_rails(positions)}",
            self._client.set_positions(
                [shade_id], primary=positions.get(Rail.PRIMARY),
                secondary=positions.get(Rail.SECONDARY), tilt=positions.get(Rail.TILT)))
        if tracker.command(self._clock.monotonic(), positions):
            self._poll(shade_id)
        self._update_shade(shade_id)

    async def move_cover(self, shade_id: int, cover: CoverSpec, openness: float) -> None:
        """Open one cover of a shade to `openness`, 0.0 closed to 1.0 open.

        Raises:
            ValueError: the cover has no rail, or `openness` is outside 0.0-1.0.
            And as `move` does.
        """
        await self.move(shade_id, cover.position(openness))

    async def tilt_cover(self, shade_id: int, cover: CoverSpec, openness: float) -> None:
        """Tilt the vanes of a cover to `openness`, 0.0 closed to 1.0 open.

        Raises:
            ValueError: the cover does not tilt, or `openness` is outside 0.0-1.0.
            And as `move` does.
        """
        await self.move(shade_id, cover.tilt_position(openness))

    async def stop(self, shade_id: int) -> None:
        """Stop a shade where it is: every rail, as the gateway stops a whole shade.

        Raises:
            KeyError: the home has no such shade.
            CommandError: the gateway reported an error for the shade.
            CannotConnectError: the gateway does not answer.
        """
        tracker = self._trackers[shade_id]
        self._interrupt(shade_id)
        await self._request(f"Stopping shade {shade_id}", self._client.stop([shade_id]))
        tracker.stop_requested(self._clock.monotonic())
        if tracker.moving:
            self._poll(shade_id)
        self._update_shade(shade_id)

    async def jog(self, shade_id: int) -> None:
        """Move a shade briefly back and forth, to show which one it is.

        Raises:
            KeyError: the home has no such shade.
            CommandError: the gateway reported an error for the shade.
            CannotConnectError: the gateway does not answer.
        """
        if shade_id not in self._trackers:
            raise KeyError(shade_id)
        self._interrupt(shade_id)
        await self._request(f"Jogging shade {shade_id}", self._client.jog(shade_id))

    async def activate_scene(self, scene_id: int) -> None:
        """Activate a scene; the moves it starts arrive on the event stream.

        Raises:
            KeyError: the home has no such scene.
            CommandError: the gateway reported an error.
            CannotConnectError: the gateway does not answer.
        """
        if all(scene.id != scene_id for scene in self.home.scenes):
            raise KeyError(scene_id)
        await self._request(f"Activating scene {scene_id}", self._client.activate_scene(scene_id))


def _rails(positions: Mapping[Rail, float]) -> str:
    return ", ".join(f"{rail}={value:.2f}" for rail, value in positions.items())


def _serial(gateway: Gateway | None) -> str | None:
    return None if gateway is None else gateway.serial


def _gateway_metadata(gateway: Gateway) -> Gateway:
    return dataclasses.replace(gateway, shade_ids=())

