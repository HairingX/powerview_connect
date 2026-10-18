"""A whole home followed by the hub, against the simulated gateway."""
import asyncio
import dataclasses
import logging
from collections.abc import AsyncIterator, Callable

import aiohttp
import pytest
from aiohttp import web

from captured_scene import EVENTS, SHADES
from conftest import until
from powerview_connect import (
    CannotConnectError,
    Capabilities,
    Change,
    CommandError,
    Connection,
    ConnectionChanged,
    CoverRole,
    GatewayChanged,
    GatewayStatus,
    HomeChanged,
    PowerViewHub,
    Rail,
    ReadOnlyError,
    ShadeChanged,
    ShadeState,
    Timing,
)
from powerview_connect._client import PowerViewClient
from powerview_connect._model import EventKind
from powerview_connect.testing import ManualClock, SimulatedGateway

P, S, T = Rail.PRIMARY, Rail.SECONDARY, Rail.TILT
TDBU, PATIO, SKYLIGHT, BLIND = 154, 271, 300, 400
TIMING = Timing(stream_idle_timeout=30.0, stall=6.0, settle=1.5, start_grace=25.0,
                min_timeout=90.0)


def _furnish(gateway: SimulatedGateway) -> None:
    gateway.add_room(65, "Living Room")
    gateway.add_shade(TDBU, "Kitchen Window", room_id=65,
                      capabilities=Capabilities.TOP_DOWN_BOTTOM_UP, shade_type=8,
                      positions={S: 0.71}, serial="0000000000000154")
    gateway.add_shade(PATIO, "Patio Door", room_id=65, positions={P: 0.4})
    gateway.add_shade(SKYLIGHT, "Skylight", room_id=65, capabilities=Capabilities.TOP_DOWN,
                      positions={P: 0.25})
    gateway.add_shade(BLIND, "Blind", room_id=65, capabilities=Capabilities.TILT_ONLY_180,
                      positions={T: 0.5})
    gateway.add_scene(216, "Morning", room_id=65, members={TDBU: {P: 0.0, S: 0.0}})


def _reads(gateway: SimulatedGateway, shade_id: int) -> int:
    return sum(request.path == f"/home/shades/{shade_id}" for request in gateway.requests)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
async def hub(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, clock: ManualClock
) -> AsyncIterator[PowerViewHub]:
    """A hub of the furnished home, loaded and running, its stream open."""
    _furnish(gateway)
    found = PowerViewHub(session, gateway.host, timing=TIMING, clock=clock)
    await found.load()
    task = asyncio.create_task(found.run())
    await until(lambda: found.connection is Connection.CONNECTED)
    yield found
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def _settle(clock: ManualClock, seconds: float) -> None:
    """Let `seconds` pass on the clock, a little at a time, so each read can answer."""
    for _ in range(round(seconds / 0.5)):
        await clock.advance(0.5)
        await asyncio.sleep(0.03)


async def _advance_until(clock: ManualClock, condition: Callable[[], bool],
                         limit: float = 120.0) -> None:
    """Let time pass on the clock, a little at a time, until `condition` holds."""
    for _ in range(round(limit / 0.5)):
        if condition():
            return
        await clock.advance(0.5)
        await asyncio.sleep(0.03)
    assert condition()


class _RecordingClock(ManualClock):
    def __init__(self) -> None:
        super().__init__()
        self.waits: list[float] = []

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)
        await super().sleep(seconds)


# -------------------------------------------------------------------- loading


@pytest.mark.parametrize("wrong", [
    {"poll_interval": 0.0}, {"stream_idle_timeout": -1.0}, {"reconnect_backoff": ()},
    {"reconnect_backoff": (1.0, -1.0)}, {"max_concurrent_reads": 0},
    {"removal_confirmations": 0}, {"stall": 0.0}, {"settle": -0.1}, {"start_grace": 0.0},
    {"min_timeout": -1.0}, {"retry_move_timeout": 0.0},
])
def test_timing_the_hub_cannot_work_with_is_refused(wrong: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        dataclasses.replace(Timing(), **wrong)


def test_a_shade_may_end_its_move_as_soon_as_it_reaches_its_target() -> None:
    assert Timing(settle=0.0).settle == 0.0


async def test_a_loaded_home_has_every_shade_gateway_and_scene(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    gateway.add_gateway("aux-1", "Bedroom Gateway", ip="192.0.2.31")
    gateway.add_gateway("aux-2", "Hall Gateway", ip="")
    hub = PowerViewHub(session, gateway.host)
    assert hub.connection is Connection.CONNECTING and not hub.available
    await hub.load()
    assert hub.connection is Connection.STREAM_DOWN and hub.available
    assert hub.host == gateway.host and hub.base_url == f"http://{gateway.host}"
    window = hub.shade(TDBU)
    assert (window.id, window.shade.name, window.positions) == (
        TDBU, "Kitchen Window", {P: 0.0, S: 0.71, T: 0.0})
    assert not window.moving and window.online and window.targets == {}
    assert [cover.role for cover in window.covers] == [CoverRole.TOP, CoverRole.BOTTOM]
    assert set(hub.shades) == {TDBU, PATIO, SKYLIGHT, BLIND}
    assert hub.gateway is not None and hub.gateway.serial == "SIMULATED-0001"
    assert hub.gateway_of(TDBU) == hub.gateway
    assert {serial: state.askable for serial, state in hub.gateways.items()} == {
        "SIMULATED-0001": True, "aux-1": True, "aux-2": False}
    assert all(state.reachable is None for state in hub.gateways.values())
    assert [scene.name for scene in hub.home.scenes] == ["Morning"]


async def test_nothing_is_known_before_the_home_is_loaded(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    hub = PowerViewHub(session, gateway.host)
    with pytest.raises(RuntimeError, match="not been loaded"):
        _ = hub.home
    with pytest.raises(RuntimeError, match="not been loaded"):
        await hub.run()
    with pytest.raises(RuntimeError, match="not been loaded"):
        await hub.refresh()


async def test_a_home_that_cannot_be_read_is_unreachable(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    await gateway.close()
    hub = PowerViewHub(session, gateway.host, timing=Timing(request_timeout=1.0))
    with pytest.raises(CannotConnectError):
        await hub.load()
    assert hub.connection is Connection.UNREACHABLE and not hub.available


async def test_loading_again_takes_what_changed(hub: PowerViewHub, gateway: SimulatedGateway) -> None:
    gateway.shades[PATIO].name = "Back Door"
    await hub.load()
    assert hub.shade(PATIO).shade.name == "Back Door"


# ------------------------------------------------------------------- following


async def test_a_move_sends_only_its_rail_and_is_followed_until_it_stops(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    states: list[ShadeState] = []
    hub.subscribe_shade(TDBU, states.append)
    await hub.move(TDBU, {S: 0.5})
    assert gateway.requests[-1].body == {"positions": {"secondary": 0.5}}
    assert hub.shade(TDBU).moving and hub.shade(TDBU).targets == {S: 0.5}
    assert hub.shade(TDBU).directions == {S: -1}
    await until(lambda: hub.shade(TDBU).eta is not None)
    gateway.move(TDBU, {S: 0.6})
    await _advance_until(clock, lambda: hub.shade(TDBU).positions[S] == 0.6)
    gateway.finish(TDBU)
    await until(lambda: not hub.shade(TDBU).moving)
    assert hub.shade(TDBU).positions[S] == 0.5 and states[-1] == hub.shade(TDBU)
    reads = _reads(gateway, TDBU)
    await _settle(clock, 3.0)
    assert _reads(gateway, TDBU) == reads


async def test_a_move_ends_without_a_stop_event_once_the_shade_is_still(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    await hub.move(PATIO, {P: 0.8})
    gateway.move(PATIO, {P: 0.8})
    await _settle(clock, 6.0)
    await until(lambda: not hub.shade(PATIO).moving)
    assert hub.shade(PATIO).positions[P] == 0.8


async def test_a_shade_that_cannot_be_read_while_it_moves_ends_its_move_in_time(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    await hub.move(PATIO, {P: 0.8})
    gateway.remove_shade(PATIO)
    await _settle(clock, 27.0)
    await until(lambda: not hub.shade(PATIO).moving)


async def test_every_shade_a_scene_moved_in_a_real_home_is_followed_until_its_own_stop(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    """The events are a real home's; the positions between them are made up, as none were logged."""
    started = {shade_id: (at, current, target) for at, kind, shade_id, current, target, _ in EVENTS
               if kind == EventKind.MOTION_STARTED}
    stopped = {shade_id: at for at, kind, shade_id, *_ in EVENTS if kind == EventKind.MOTION_STOPPED}
    gateway.add_room(1, "Home")
    for shade_id, (shade_type, capabilities, battery_status, _) in SHADES.items():
        gateway.add_shade(shade_id, f"Shade {shade_id}", room_id=1, capabilities=capabilities,
                          shade_type=shade_type, battery_status=battery_status,
                          positions=started[shade_id][1])
    hub = PowerViewHub(session, gateway.host, timing=TIMING, clock=clock)
    await hub.load()
    task = asyncio.create_task(hub.run())
    await until(lambda: hub.connection is Connection.CONNECTED)
    begin = clock.monotonic()
    moved: set[int] = set()
    ended: dict[int, float] = {}

    def note_end(change: Change) -> None:
        if isinstance(change, ShadeChanged):
            if change.state.moving:
                moved.add(change.state.shade.id)
            elif change.state.shade.id in moved:
                ended.setdefault(change.state.shade.id, clock.monotonic() - begin)

    hub.subscribe(note_end)
    pending = list(EVENTS)
    for step in range(round(92 / 0.5)):
        now = step * 0.5
        for shade_id, (at, current, target) in started.items():
            if at <= now < stopped[shade_id]:
                done = (now - at) / (stopped[shade_id] - at)
                gateway.move(shade_id, {P: round(current[P] + done * (target[P] - current[P]), 2)})
        while pending and pending[0][0] <= now:
            _, kind, shade_id, _, target, eta = pending.pop(0)
            if kind == EventKind.MOTION_STARTED:
                gateway.emit(kind, shade_id, targetPositions={**target, "etaInSeconds": eta})
            elif kind == EventKind.MOTION_STOPPED:
                gateway.move(shade_id, started[shade_id][2])
                gateway.emit(kind, shade_id)
            else:
                gateway.emit(kind, shade_id, batteryLevel=1)
        await clock.advance(0.5)
        await asyncio.sleep(0.03)
    for shade_id, at in stopped.items():
        assert at <= ended[shade_id] <= at + 1.0, shade_id
        assert hub.shade(shade_id).positions[P] == 1.0
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_read_during_a_move_takes_signal_strength_and_battery(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    gateway.shades[PATIO].signal_strength = -71
    gateway.shades[PATIO].battery_status = 1
    await hub.move(PATIO, {P: 0.8})
    await _advance_until(clock, lambda: hub.shade(PATIO).shade.signal_strength == -71)
    assert hub.shade(PATIO).shade.battery_status == 1


async def test_covers_move_and_tilt_by_how_far_they_open(
    hub: PowerViewHub, gateway: SimulatedGateway
) -> None:
    (skylight,) = hub.shade(SKYLIGHT).covers
    await hub.move_cover(SKYLIGHT, skylight, 1.0)
    assert gateway.requests[-1].body == {"positions": {"primary": 0.0}}
    (vanes,) = hub.shade(BLIND).covers
    await hub.tilt_cover(BLIND, vanes, 0.3)
    assert gateway.requests[-1].body == {"positions": {"tilt": 0.3}}
    with pytest.raises(ValueError, match="no rail"):
        await hub.move_cover(BLIND, vanes, 0.5)


async def test_a_stop_ends_the_move_where_the_shade_is(
    hub: PowerViewHub, gateway: SimulatedGateway
) -> None:
    await hub.move(PATIO, {P: 0.9})
    await hub.stop(PATIO)
    assert gateway.requests[-1].path == "/home/shades/stop"
    await until(lambda: not hub.shade(PATIO).moving)
    await hub.stop(PATIO)
    assert not hub.shade(PATIO).moving


async def test_a_stop_without_a_stop_event_is_followed_until_the_shade_is_still(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    await hub.move(PATIO, {P: 0.9})
    gateway.refuse_streams = True
    gateway.drop_streams()
    await until(lambda: hub.connection is Connection.STREAM_DOWN)
    await hub.stop(PATIO)
    assert hub.shade(PATIO).moving and hub.shade(PATIO).targets == {}
    await _settle(clock, 30.0)
    await until(lambda: not hub.shade(PATIO).moving)


async def test_a_jog_and_a_scene_go_to_the_gateway(
    hub: PowerViewHub, gateway: SimulatedGateway
) -> None:
    await hub.jog(PATIO)
    assert gateway.requests[-1].body == {"motion": "jog"}
    await hub.activate_scene(216)
    assert gateway.requests[-1].path == "/home/scenes/216/activate"


async def test_a_command_for_a_shade_or_scene_not_known_is_refused_before_it_is_sent(
    hub: PowerViewHub, gateway: SimulatedGateway
) -> None:
    sent = len(gateway.requests)
    for command in (hub.move(9, {P: 0.5}), hub.stop(9), hub.jog(9), hub.activate_scene(9)):
        with pytest.raises(KeyError):
            await command
    assert len(gateway.requests) == sent


async def test_a_read_only_hub_follows_the_home_and_refuses_every_command(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    hub = PowerViewHub(session, gateway.host, read_only=True)
    await hub.load()
    for command in (hub.move(PATIO, {P: 0.5}), hub.stop(PATIO), hub.jog(PATIO),
                    hub.activate_scene(216)):
        with pytest.raises(ReadOnlyError):
            await command
    assert all(request.method == "GET" for request in gateway.requests)
    assert hub.read_only and hub.available


async def test_an_error_the_gateway_reports_for_a_command_is_raised(
    hub: PowerViewHub, gateway: SimulatedGateway
) -> None:
    gateway.command_errors[PATIO] = 7
    with pytest.raises(CommandError) as raised:
        await hub.move(PATIO, {P: 0.5})
    assert raised.value.shade_ids == {PATIO}
    assert hub.available


async def test_a_command_the_gateway_does_not_answer_makes_it_unreachable(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    hub = PowerViewHub(session, gateway.host, timing=Timing(request_timeout=1.0))
    await hub.load()
    await gateway.close()
    with pytest.raises(CannotConnectError):
        await hub.stop(PATIO)
    assert hub.connection is Connection.UNREACHABLE


async def test_events_say_when_a_shade_goes_offline_and_comes_back(
    hub: PowerViewHub, gateway: SimulatedGateway
) -> None:
    gateway.set_online(PATIO, False)
    await until(lambda: not hub.shade(PATIO).online)
    gateway.set_online(PATIO, True)
    await until(lambda: hub.shade(PATIO).online)


async def test_a_shade_online_event_says_where_a_shade_standing_still_is(
    hub: PowerViewHub, gateway: SimulatedGateway
) -> None:
    """A shade sliding down by itself reports where it is in these, and in no motion-started."""
    gateway.shades[PATIO].positions[P] = 0.95
    gateway.set_online(PATIO, True)
    await until(lambda: hub.shade(PATIO).positions[P] == 0.95)
    assert not hub.shade(PATIO).moving


async def test_events_of_other_kinds_or_shades_not_known_change_nothing(
    hub: PowerViewHub, gateway: SimulatedGateway
) -> None:
    changes: list[Change] = []
    hub.subscribe(changes.append)
    gateway.emit(EventKind.BATTERY_ALERT, PATIO, batteryLevel=1)
    gateway.emit(EventKind.MOTION_STARTED, 9)
    gateway.set_online(PATIO, False)
    await until(lambda: not hub.shade(PATIO).online)
    assert len(changes) == 1


# ------------------------------------------------------------------ the stream


async def test_a_dropped_stream_is_reopened_and_what_happened_meanwhile_is_read(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    gateway.drop_streams()
    await until(lambda: hub.connection is Connection.STREAM_DOWN)
    gateway.move(PATIO, {P: 0.9})
    await clock.advance(TIMING.reconnect_backoff[0])
    await until(lambda: hub.connection is Connection.CONNECTED)
    await until(lambda: hub.shade(PATIO).positions[P] == 0.9)
    assert hub.available


async def test_a_silent_stream_is_reopened_without_the_gateway_going_unavailable(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    changes: list[Change] = []
    hub = PowerViewHub(session, gateway.host, timing=Timing(stream_idle_timeout=0.2),
                       clock=ManualClock())
    hub.subscribe(changes.append)
    await hub.load()
    async with asyncio.TaskGroup() as group:
        task = group.create_task(hub.run())
        await gateway.wait_for_stream()
        await gateway.wait_for_stream()
        task.cancel()
    assert all(not isinstance(change, ConnectionChanged)
               or change.connection is not Connection.UNREACHABLE for change in changes)


async def test_a_gateway_that_stops_answering_is_unreachable_until_it_answers(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    await gateway.close()
    await _advance_until(clock, lambda: hub.connection is Connection.UNREACHABLE)
    assert "does not answer" in caplog.text
    await gateway.start()
    await _advance_until(clock, lambda: hub.connection is Connection.CONNECTED)
    assert "The PowerView gateway at" in caplog.text and "answers\n" in caplog.text + "\n"


async def test_the_waits_between_attempts_grow_until_a_stream_stays_open(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    clock = _RecordingClock()
    hub = PowerViewHub(session, gateway.host, clock=clock, timing=dataclasses.replace(
        TIMING, gateway_interval=61.0, refresh_interval=601.0))
    await hub.load()
    gateway.refuse_streams = True
    task = asyncio.create_task(hub.run())
    await _advance_until(clock, lambda: sum(
        request.path == "/home/shades/events" for request in gateway.requests) >= 4, limit=30)
    backoff = [wait for wait in clock.waits if wait in TIMING.reconnect_backoff]
    assert backoff[:3] == list(TIMING.reconnect_backoff[:3])
    assert hub.available and hub.connection is Connection.STREAM_DOWN
    gateway.refuse_streams = False
    await _advance_until(clock, lambda: hub.connection is Connection.CONNECTED)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_stream_that_breaks_is_reopened_without_the_gateway_going_unavailable(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    gateway.break_streams()
    await until(lambda: hub.connection is Connection.STREAM_DOWN)
    assert hub.available
    await _advance_until(clock, lambda: hub.connection is Connection.CONNECTED)


async def test_a_stream_that_stays_open_long_enough_starts_the_waits_over(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    await clock.advance(TIMING.stream_healthy_after)
    gateway.drop_streams()
    await until(lambda: hub.connection is Connection.STREAM_DOWN)
    await clock.advance(TIMING.reconnect_backoff[0])
    await until(lambda: hub.connection is Connection.CONNECTED)


# ------------------------------------------------------------------- refreshing


async def test_a_refresh_takes_positions_changed_without_an_event(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    gateway.move(PATIO, {P: 0.9})
    await clock.advance(TIMING.refresh_interval)
    await until(lambda: hub.shade(PATIO).positions[P] == 0.9)


async def test_a_shade_added_to_the_home_is_added_and_one_removed_is_removed_on_the_second_read(
    hub: PowerViewHub, gateway: SimulatedGateway
) -> None:
    homes: list[HomeChanged] = []
    names: list[str] = []

    def changed(change: Change) -> None:
        if isinstance(change, HomeChanged):
            homes.append(change)
            names.extend(hub.shade(shade_id).shade.name for shade_id in change.diff.added_shades)

    hub.subscribe(changed)
    gateway.add_shade(500, "New Shade", room_id=65)
    gateway.remove_shade(BLIND)
    await hub.refresh()
    assert homes[-1].diff.added_shades == {500} and not homes[-1].diff.removed_shades
    assert names == ["New Shade"], "a listener reads a new shade's state when it is told of it"
    assert 500 in hub.shades and BLIND in hub.shades
    await hub.refresh()
    assert homes[-1].diff.removed_shades == {BLIND}
    assert BLIND not in hub.shades and BLIND not in hub.home.shades


async def test_a_shade_missing_from_one_read_only_is_kept(
    hub: PowerViewHub, gateway: SimulatedGateway
) -> None:
    removed = gateway.shades.pop(BLIND)
    await hub.refresh()
    gateway.shades[BLIND] = removed
    await hub.refresh()
    gateway.shades.pop(BLIND)
    await hub.refresh()
    assert BLIND in hub.shades


async def test_a_renamed_shade_and_a_changed_scene_are_reported_as_changed(
    hub: PowerViewHub, gateway: SimulatedGateway
) -> None:
    homes: list[HomeChanged] = []
    names: list[str] = []

    def changed(change: Change) -> None:
        if isinstance(change, HomeChanged):
            homes.append(change)
            names.extend(hub.shade(shade_id).shade.name for shade_id in change.diff.changed_shades)

    hub.subscribe(changed)
    gateway.shades[PATIO].name = "Back Door"
    gateway.scenes[216].name = "Early Morning"
    gateway.add_scene(217, "Evening", room_id=65, members={})
    await hub.refresh()
    diff = homes[-1].diff
    assert (diff.changed_shades, diff.changed_scenes, diff.added_scenes) == (
        {PATIO}, {216}, {217})
    assert hub.shade(PATIO).shade.name == "Back Door" and names == ["Back Door"]
    gateway.scenes.clear()
    await hub.refresh()
    await hub.refresh()
    assert homes[-1].diff.removed_scenes == {216, 217}


async def test_a_shade_moved_to_another_gateway_and_gateways_coming_and_going_are_reported(
    hub: PowerViewHub, gateway: SimulatedGateway, session: aiohttp.ClientSession
) -> None:
    homes: list[HomeChanged] = []
    hub.subscribe(lambda change: homes.append(change) if isinstance(change, HomeChanged) else None)
    async with SimulatedGateway(serial="aux-1", primary=False) as auxiliary:
        gateway.add_gateway("aux-1", "Bedroom Gateway", ip=auxiliary.host)
        gateway.shades[PATIO].gateway = "aux-1"
        await hub.refresh()
        diff = homes[-1].diff
        assert (diff.added_gateways, diff.changed_shades) == ({"aux-1"}, {PATIO})
        serving = hub.gateway_of(PATIO)
        assert serving is not None and serving.serial == "aux-1"
        await hub.refresh_gateways()
        assert hub.gateways["aux-1"].reachable and hub.gateways["aux-1"].status is not None
        gateway.gateways[1].name = "Guest Gateway"
        await hub.refresh()
        assert homes[-1].diff.changed_gateways == {"aux-1"}
        assert hub.gateways["aux-1"].reachable, "a renamed gateway keeps its state"
        gateway.gateways[1].ip = "192.0.2.31"
        await hub.refresh()
        assert hub.gateways["aux-1"].reachable is None, "a gateway at a new address is asked anew"
        assert hub.gateways["aux-1"].status is not None
        gateway.gateways[1].ip = ""
        await hub.refresh()
        assert not hub.gateways["aux-1"].askable
        del gateway.gateways[1]
        await hub.refresh()
        await hub.refresh()
        assert homes[-1].diff.removed_gateways == {"aux-1"} and "aux-1" not in hub.gateways


async def test_a_refresh_that_fails_while_running_is_tried_again_later(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    gateway.refuse_streams = True
    await gateway.close()
    await clock.advance(TIMING.refresh_interval)
    await until(lambda: hub.connection is Connection.UNREACHABLE)


# --------------------------------------------------------------------- gateways


async def test_every_gateway_is_asked_of_its_state_and_one_not_answering_keeps_its_status(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    async with SimulatedGateway(serial="aux-1", primary=False) as auxiliary:
        gateway.add_gateway("aux-1", "Bedroom Gateway", ip=auxiliary.host)
        hub = PowerViewHub(session, gateway.host, timing=Timing(request_timeout=1.0))
        await hub.load()
        changed: list[GatewayChanged] = []
        hub.subscribe_gateway("aux-1", lambda state: changed.append(GatewayChanged(state=state)))
        await hub.refresh_gateways()
        assert hub.gateways["SIMULATED-0001"].reachable
        assert hub.gateways["aux-1"].reachable and changed[-1].state.status is not None
        await hub.refresh_gateways()
        assert len(changed) == 1
        await auxiliary.close()
        await hub.refresh_gateways()
        state = hub.gateways["aux-1"]
        assert state.reachable is False and state.status is not None
        assert all(request.method == "GET" for request in auxiliary.requests)


async def test_a_gateway_that_leaves_the_home_while_it_is_asked_is_forgotten(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    asked, answer = asyncio.Event(), asyncio.Event()

    async def slow(_: web.Request) -> web.Response:
        asked.set()
        await answer.wait()
        return web.json_response({"config": {"serialNumber": "aux-1"}})

    app = web.Application()
    app.router.add_get("/gateway", slow)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        gateway.add_gateway("aux-1", "Bedroom Gateway", ip=site.name.removeprefix("http://"))
        hub = PowerViewHub(session, gateway.host)
        await hub.load()
        asking = asyncio.create_task(hub.refresh_gateways())
        await asked.wait()
        del gateway.gateways[1]
        await hub.refresh()
        await hub.refresh()
        answer.set()
        await asking
        assert "aux-1" not in hub.gateways
    finally:
        await runner.cleanup()


async def test_an_error_that_is_not_a_gateways_is_raised_after_the_others_are_taken(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, monkeypatch: pytest.MonkeyPatch
) -> None:
    _furnish(gateway)
    async with SimulatedGateway(serial="aux-1", primary=False) as auxiliary:
        gateway.add_gateway("aux-1", "Bedroom Gateway", ip=auxiliary.host)
        hub = PowerViewHub(session, gateway.host)
        await hub.load()
        answer = PowerViewClient.get_gateway

        async def broken(client: PowerViewClient) -> GatewayStatus:
            if client.host == gateway.host:
                raise RuntimeError("a bug")
            return await answer(client)

        monkeypatch.setattr(PowerViewClient, "get_gateway", broken)
        with pytest.raises(RuntimeError, match="a bug"):
            await hub.refresh_gateways()
        assert hub.gateways["aux-1"].reachable


async def test_a_failing_task_of_the_hub_is_logged_and_goes_on(
    hub: PowerViewHub, clock: ManualClock, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[int] = []

    async def broken(client: PowerViewClient) -> None:
        calls.append(1)
        raise RuntimeError("a bug")

    monkeypatch.setattr(PowerViewClient, "get_gateway", broken)
    await _advance_until(clock, lambda: len(calls) >= 2, limit=180)
    assert "goes on" in caplog.text


async def test_a_failing_read_of_moving_shades_is_logged_and_goes_on(
    hub: PowerViewHub, clock: ManualClock, monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(shade_id: int) -> None:
        raise RuntimeError("a bug")

    monkeypatch.setattr(hub, "_read_moving", broken)
    await hub.move(PATIO, {P: 0.9})
    await _advance_until(clock, lambda: "moving shades failed" in caplog.text)


async def test_a_failing_event_is_logged_and_the_stream_reopened(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock,
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    def broken(event: object) -> None:
        raise RuntimeError("a bug")

    monkeypatch.setattr(hub, "_handle", broken)
    gateway.set_online(PATIO, False)
    await until(lambda: "event stream failed" in caplog.text)
    await clock.advance(TIMING.reconnect_backoff[0])
    await gateway.wait_for_stream()


async def test_a_shade_that_stops_while_its_read_waits_its_turn_is_not_read(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    _furnish(gateway)
    gateway.shade_read_delay = 0.3
    hub = PowerViewHub(session, gateway.host, clock=clock,
                       timing=dataclasses.replace(TIMING, max_concurrent_reads=1))
    await hub.load()
    task = asyncio.create_task(hub.run())
    await until(lambda: hub.connection is Connection.CONNECTED)
    await hub.move(PATIO, {P: 0.9})
    await hub.move(SKYLIGHT, {P: 0.9})
    await _advance_until(clock, lambda: _reads(gateway, PATIO) == 1)
    gateway.finish(SKYLIGHT)
    await until(lambda: not hub.shade(SKYLIGHT).moving)
    await asyncio.sleep(0.5)
    assert _reads(gateway, SKYLIGHT) == 0
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_shade_that_leaves_the_home_while_it_is_read_is_forgotten(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    _furnish(gateway)
    gateway.shade_read_delay = 0.5
    hub = PowerViewHub(session, gateway.host, clock=clock, timing=TIMING)
    await hub.load()
    task = asyncio.create_task(hub.run())
    await until(lambda: hub.connection is Connection.CONNECTED)
    await hub.move(PATIO, {P: 0.9})
    await _advance_until(clock, lambda: _reads(gateway, PATIO) == 1)
    # Out of every room, so the home no longer lists it, while it still answers.
    gateway.shades[PATIO].room_id = 999
    await hub.refresh()
    await hub.refresh()
    await asyncio.sleep(0.7)
    assert PATIO not in hub.shades
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


async def test_a_move_to_where_the_shade_is_reads_nothing(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock
) -> None:
    gateway.refuse_streams = True
    gateway.drop_streams()
    await until(lambda: hub.connection is Connection.STREAM_DOWN)
    await hub.move(PATIO, {P: 0.4})
    assert not hub.shade(PATIO).moving
    await _settle(clock, 2.0)
    assert _reads(gateway, PATIO) == 0


async def test_a_manual_clock_wakes_each_sleep_when_it_is_due() -> None:
    clock = ManualClock(now=10.0)
    woken: list[float] = []

    async def sleeper(seconds: float) -> None:
        await clock.sleep(seconds)
        woken.append(clock.monotonic())

    tasks = [asyncio.create_task(sleeper(seconds)) for seconds in (3.0, 1.0, 2.0)]
    await asyncio.sleep(0)
    assert clock.sleepers == 3
    tasks[2].cancel()
    await asyncio.gather(tasks[2], return_exceptions=True)
    assert clock.sleepers == 2
    await clock.advance(2.5)
    assert woken == [11.0] and clock.now == 12.5
    await clock.advance(1.0)
    assert woken == [11.0, 13.0]
    await asyncio.gather(*tasks[:2])


# -------------------------------------------------------------------- listening


async def test_a_listener_that_fails_is_logged_and_the_others_are_still_called(
    hub: PowerViewHub, caplog: pytest.LogCaptureFixture
) -> None:
    def broken(change: Change) -> None:
        raise RuntimeError("a bug")

    seen: list[Change] = []
    hub.subscribe(broken)
    unsubscribe = hub.subscribe(seen.append)
    await hub.move(PATIO, {P: 0.9})
    assert seen and "listener failed" in caplog.text
    unsubscribe()
    count = len(seen)
    await hub.stop(PATIO)
    assert len(seen) == count


async def test_changes_can_be_iterated(hub: PowerViewHub, gateway: SimulatedGateway) -> None:
    async def first() -> Change:
        async for change in hub.changes():
            return change
        raise AssertionError  # pragma: no cover

    waiting = asyncio.create_task(first())
    await asyncio.sleep(0)
    gateway.set_online(PATIO, False)
    change = await asyncio.wait_for(waiting, 5)
    assert isinstance(change, ShadeChanged) and not change.state.online


async def test_a_shade_or_gateway_listener_can_be_stopped(hub: PowerViewHub) -> None:
    seen: list[object] = []
    stop_shade = hub.subscribe_shade(PATIO, seen.append)
    stop_gateway = hub.subscribe_gateway("SIMULATED-0001", seen.append)
    stop_shade()
    stop_gateway()
    await hub.move(PATIO, {P: 0.9})
    await hub.refresh_gateways()
    assert seen == []


# ------------------------------------------------------------------- the rest


async def test_the_snapshot_holds_no_address_serial_number_or_bluetooth_name(
    hub: PowerViewHub, gateway: SimulatedGateway
) -> None:
    await hub.refresh_gateways()
    text = repr(hub.snapshot())
    for secret in (gateway.host, "127.0.0.1", "SIMULATED-0001", "0000000000000154", "SIM:"):
        assert secret not in text, secret
    assert hub.snapshot()["connection"] == "connected"
    assert "hardware_version" in text


async def test_a_hub_used_as_a_context_manager_follows_the_home_until_it_exits(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    async with PowerViewHub(session, gateway.host) as hub:
        await until(lambda: hub.connection is Connection.CONNECTED)
        gateway.set_online(PATIO, False)
        await until(lambda: not hub.shade(PATIO).online)
    assert hub.connection is Connection.STREAM_DOWN
    await until(lambda: gateway.streams == 0)
    await hub.close()


async def test_the_hub_logs_every_event_and_request_with_its_time(
    hub: PowerViewHub, gateway: SimulatedGateway, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="powerview_connect")
    await hub.move(PATIO, {P: 0.9})
    await until(lambda: "Event: ShadeEvent(kind='motion-started'" in caplog.text)
    assert "Moving shade 271 to primary=0.90 took" in caplog.text
    assert "SIM:" not in caplog.text and "0000000000000154" not in caplog.text
