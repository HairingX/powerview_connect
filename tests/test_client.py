"""The client against the simulated gateway, which answers as a real Gen 3 gateway was measured to."""
import asyncio
from collections.abc import AsyncIterator

import aiohttp
import pytest
from aiohttp import web

from powerview_connect import (
    CannotConnectError,
    Capabilities,
    CommandError,
    EventKind,
    PowerViewClient,
    Rail,
    ReadOnlyError,
    ResponseError,
    ShadeEvent,
)
from powerview_connect.testing import Request, SimulatedGateway

P, S, T = Rail.PRIMARY, Rail.SECONDARY, Rail.TILT


def _furnish(gateway: SimulatedGateway) -> None:
    gateway.add_room(65, "Main Room")
    gateway.add_shade(154, "Kitchen Middle Curtain", room_id=65,
                      capabilities=Capabilities.TOP_DOWN_BOTTOM_UP, shade_type=8,
                      positions={S: 0.71}, serial="0000000000000154")
    gateway.add_shade(271, "Living Room East Curtain", room_id=65, positions={T: 1.0},
                      signal_strength=None)
    gateway.add_scene(216, "Kitchen Dining", room_id=65, members={154: {P: 0.0, S: 0.458}})


async def _next(events: AsyncIterator[ShadeEvent]) -> ShadeEvent:
    return await asyncio.wait_for(anext(events), 5)


def _commands(gateway: SimulatedGateway) -> list[Request]:
    return [request for request in gateway.requests if request.method == "PUT"]


async def test_the_home_lists_what_the_gateway_holds(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    gateway.add_gateway("aux-1", "Bedroom Gateway", ip="192.0.2.31")
    home = await client.get_home()
    assert home.home_id == "simulated-home"
    assert [(g.serial, g.is_primary) for g in home.gateways] == [
        ("SIMULATED-0001", True), ("aux-1", False)]
    assert home.gateway_at(client.host) == home.primary_gateway
    shade = home.shades[154]
    assert (shade.name, shade.room_name, shade.capability, shade.type, shade.firmware) == (
        "Kitchen Middle Curtain", "Main Room", Capabilities.TOP_DOWN_BOTTOM_UP, 8, "3.0.433")
    assert shade.positions == {P: 0.0, S: 0.71, T: 0.0}
    assert shade.serial == "0000000000000154" and home.shades[271].serial is None
    assert (shade.signal_strength, shade.battery_status) == (-66, 3)
    gateway.add_shade(5, "Battery Shade", room_id=65, power_type=0, battery_status=1)
    battery = (await client.get_home()).shades[5]
    assert (battery.hard_wired, battery.battery_status) == (False, 1)
    assert [(scene.id, scene.name) for scene in home.scenes] == [(216, "Kitchen Dining")]


async def test_a_shade_reports_its_positions_as_they_change_during_a_move(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    assert (await client.get_shade(271)).signal_strength is None
    gateway.shades[154].signal_strength = -71
    assert (await client.get_shade(154)).signal_strength == -71
    await client.set_positions([154], primary=0.25)
    gateway.move(154, {P: 0.1})
    assert (await client.get_shade(154)).positions[P] == 0.1
    gateway.finish(154)
    assert (await client.get_shade(154)).positions == {P: 0.25, S: 0.71, T: 0.0}


async def test_motion_stopped_without_a_move_reports_where_the_shade_is(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    async with client.shade_events(idle_timeout=5) as events:
        await gateway.wait_for_stream()
        gateway.finish(154)
        stopped = await _next(events)
    assert (stopped.kind, stopped.current) == (EventKind.MOTION_STOPPED, {P: 0.0, S: 0.71, T: 0.0})


async def test_a_move_sends_only_the_rails_given(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    """A rail left out is not changed; sending both from a cache would undo another move."""
    _furnish(gateway)
    await client.set_positions([154], primary=0.2)
    await client.set_positions([154], secondary=0.5)
    assert [(r.path, r.query, r.body) for r in _commands(gateway)] == [
        ("/home/shades/positions", {"ids": "154"}, {"positions": {"primary": 0.2}}),
        ("/home/shades/positions", {"ids": "154"}, {"positions": {"secondary": 0.5}}),
    ]
    gateway.finish(154)
    assert (await client.get_shade(154)).positions == {P: 0.2, S: 0.5, T: 0.0}


async def test_both_rails_and_several_shades_go_in_one_request(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    await client.set_positions([154, 271], primary=0.3, secondary=0.4, tilt=0.5, velocity=0.1)
    assert [(r.query, r.body) for r in _commands(gateway)] == [({"ids": "154,271"}, {
        "positions": {"primary": 0.3, "secondary": 0.4, "tilt": 0.5, "velocity": 0.1}})]


async def test_the_shade_stops_a_rail_where_it_meets_the_other_one(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    gateway.shades[154].positions.update({P: 0.5, S: 0.0})
    async with client.shade_events(idle_timeout=5) as events:
        await gateway.wait_for_stream()
        await client.set_positions([154], secondary=0.6)
        await _next(events)
        assert (await _next(events)).target[S] == 0.6
    gateway.finish(154)
    assert (await client.get_shade(154)).positions[S] == 0.5
    await client.set_positions([154], primary=0.8)
    gateway.finish(154)
    assert (await client.get_shade(154)).positions[P] == 0.5


@pytest.mark.parametrize(("given", "message"), [({}, "no position"), ({"primary": 1.5}, "0.0 to 1.0")])
async def test_a_move_without_valid_positions_is_refused_before_it_is_sent(
    client: PowerViewClient, gateway: SimulatedGateway, given: dict[str, float], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        await client.set_positions([154], **given)
    assert _commands(gateway) == []


async def test_a_command_needs_a_shade(client: PowerViewClient) -> None:
    with pytest.raises(ValueError, match="at least one shade"):
        await client.stop([])


async def test_an_error_the_gateway_reports_for_a_shade_is_raised(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    gateway.command_errors[154] = 7
    with pytest.raises(CommandError, match="shade 154") as raised:
        await client.set_positions([154], primary=0.5)
    assert raised.value.errors == ["shade 154 (SIM:0154) err=7"]


async def test_events_announce_a_move_and_its_end(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    async with client.shade_events(idle_timeout=5) as events:
        await gateway.wait_for_stream()
        await client.set_positions([154], secondary=0.5)
        alert, started = await _next(events), await _next(events)
        assert (alert.kind, alert.shade_id) == (EventKind.BATTERY_ALERT, 154)
        assert started.kind == EventKind.MOTION_STARTED
        assert started.current == {P: 0.0, S: 0.71, T: 0.0}
        assert started.target == {P: 0.0, S: 0.5, T: 0.0}
        assert started.eta == 4
        gateway.finish(154)
        stopped = await _next(events)
        assert (stopped.kind, stopped.current[S]) == (EventKind.MOTION_STOPPED, 0.5)


async def test_a_stop_ends_the_move_where_the_shade_is(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    async with client.shade_events(idle_timeout=5) as events:
        await gateway.wait_for_stream()
        await client.set_positions([154], secondary=0.1)
        gateway.move(154, {S: 0.4})
        await client.stop([154])
        kinds = [(await _next(events)).kind for _ in range(3)]
        assert kinds == [EventKind.BATTERY_ALERT, EventKind.MOTION_STARTED, EventKind.MOTION_STOPPED]
    assert gateway.shades[154].target is None
    assert _commands(gateway)[-1].path == "/home/shades/stop"


async def test_a_scene_moves_its_shades(client: PowerViewClient, gateway: SimulatedGateway) -> None:
    _furnish(gateway)
    gateway.add_scene(9, "Gone", room_id=65, members={999: {P: 1.0}})
    async with client.shade_events(idle_timeout=5) as events:
        await gateway.wait_for_stream()
        await client.activate_scene(216)
        await client.activate_scene(9)
        await _next(events)
        started = await _next(events)
    assert started.target[S] == 0.458
    assert _commands(gateway)[0].path == "/home/scenes/216/activate"


async def test_a_scene_not_known_is_an_error(client: PowerViewClient) -> None:
    with pytest.raises(ResponseError, match="HTTP 404"):
        await client.activate_scene(1)


async def test_a_shade_not_known_is_an_error(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    with pytest.raises(ResponseError, match="HTTP 404"):
        await client.get_shade(1)
    with pytest.raises(ResponseError, match="HTTP 400"):
        await client.stop([1])
    gateway.add_room(1, "Room")
    gateway.add_shade(1, "Shade", room_id=1)
    gateway.remove_shade(1)
    with pytest.raises(ResponseError, match="HTTP 404"):
        await client.get_shade(1)


async def test_events_say_when_a_shade_goes_offline_and_comes_back(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    async with client.shade_events(idle_timeout=5) as events:
        await gateway.wait_for_stream()
        gateway.set_online(271, False)
        gateway.set_online(271, True)
        gateway.emit(EventKind.SHADE_OFFLINE, 5)
        assert [(await _next(events)).kind for _ in range(3)] == [
            EventKind.SHADE_OFFLINE, EventKind.SHADE_ONLINE, EventKind.SHADE_OFFLINE]


async def test_events_end_when_the_gateway_drops_the_stream(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    async with client.shade_events(idle_timeout=5) as events:
        await gateway.wait_for_stream()
        assert gateway.streams == 1
        gateway.drop_streams()
        assert [event async for event in events] == []


async def test_a_stream_closed_by_the_client_is_no_longer_open(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    async with client.shade_events(idle_timeout=5):
        await gateway.wait_for_stream()
    for _ in range(100):
        if gateway.streams == 0:
            break
        await asyncio.sleep(0.01)
    assert gateway.streams == 0


async def test_a_silent_stream_is_taken_as_broken(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    async with client.shade_events(idle_timeout=0.2) as events:
        with pytest.raises(CannotConnectError, match="shade events"):
            await _next(events)


async def test_a_gateway_reports_its_own_state(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    status = await client.get_gateway()
    assert (status.serial, status.is_primary, status.firmware, status.hardware_version) == (
        "SIMULATED-0001", True, "3.2.59", "3.0.4")
    assert (status.wifi, status.internet_connected) == (False, True)
    gateway.interface, gateway.internet_state = "wlan0", "Disconnected"
    status = await client.get_gateway()
    assert (status.wifi, status.internet_connected) == (True, False)
    assert gateway.requests[-1].path == "/gateway"


async def test_an_auxiliary_gateway_answers_that_it_is_not_the_primary(
    session: aiohttp.ClientSession,
) -> None:
    async with SimulatedGateway(serial="aux-1", primary=False) as auxiliary:
        status = await PowerViewClient(session, auxiliary.host, read_only=True).get_gateway()
    assert (status.serial, status.is_primary) == ("aux-1", False)


async def test_the_home_lists_each_shade_under_the_gateway_that_serves_it(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    gateway.add_gateway("aux-1", "Bedroom Gateway", ip="192.0.2.31")
    gateway.add_shade(163, "Bathroom 2 Curtain", room_id=65, gateway="aux-1")
    primary, auxiliary = (await client.get_home()).gateways
    assert (primary.id, primary.shade_ids) == (1, (154, 271))
    assert (auxiliary.id, auxiliary.shade_ids) == (2, (163,))
    assert auxiliary.radio_firmware == ("3.0.43", "3.0.43")


async def test_an_offline_gateway_cannot_be_reached_until_it_is_back(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    await gateway.close()
    with pytest.raises(CannotConnectError, match="GET /home"):
        await client.get_home()
    with pytest.raises(CannotConnectError, match="GET /gateway"):
        await client.get_gateway()
    with pytest.raises(CannotConnectError, match="events"):
        async with client.shade_events(idle_timeout=1):
            pass  # pragma: no cover
    await gateway.start()
    assert (await client.get_home()).home_id == "simulated-home"


async def test_a_gateway_closing_mid_stream_ends_or_breaks_the_events(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    async with client.shade_events(idle_timeout=5) as events:
        await gateway.wait_for_stream()
        await gateway.close()
        assert [event async for event in events] == []


async def test_a_read_only_client_refuses_every_command_before_it_is_sent(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    client = PowerViewClient(session, f"http://{gateway.host}/", read_only=True)
    assert client.host == gateway.host and client.base_url == f"http://{gateway.host}"
    assert client.read_only
    for command in (client.set_positions([154], primary=0.1), client.stop([154]),
                    client.activate_scene(216), client.jog(154)):
        with pytest.raises(ReadOnlyError):
            await command
    assert _commands(gateway) == []
    assert (await client.get_home()).shades


@pytest.fixture
async def odd_gateway() -> AsyncIterator[str]:
    """A server answering as no gateway does."""
    async def empty(_: web.Request) -> web.Response:
        return web.Response(status=204)

    async def text(_: web.Request) -> web.Response:
        return web.Response(text="<html>")

    async def array(_: web.Request) -> web.Response:
        return web.json_response([1])

    app = web.Application()
    app.router.add_get("/home", empty)
    app.router.add_get("/home/shades/1", text)
    app.router.add_get("/home/shades/2", array)
    app.router.add_get("/home/shades/events", array)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    yield site.name.removeprefix("http://")
    await runner.cleanup()


async def test_answers_that_are_not_the_documents_asked_for_are_errors(
    session: aiohttp.ClientSession, odd_gateway: str
) -> None:
    client = PowerViewClient(session, odd_gateway)
    with pytest.raises(ResponseError, match="not an object"):
        await client.get_home()
    with pytest.raises(ResponseError, match="not JSON"):
        await client.get_shade(1)
    with pytest.raises(ResponseError, match="not an object"):
        await client.get_shade(2)


async def test_a_stream_the_gateway_refuses_is_an_error(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    async def refuse(_: web.Request) -> web.Response:
        return web.Response(status=503)

    app = web.Application()
    app.router.add_get("/home/shades/events", refuse)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        client = PowerViewClient(session, site.name)
        with pytest.raises(ResponseError, match="HTTP 503"):
            async with client.shade_events(idle_timeout=1):
                pass  # pragma: no cover
    finally:
        await runner.cleanup()


async def test_a_simulated_gateway_has_no_address_before_it_starts() -> None:
    with pytest.raises(RuntimeError, match="not started"):
        _ = SimulatedGateway().host


async def test_a_move_without_positions_is_refused_by_the_simulated_gateway(
    session: aiohttp.ClientSession, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    async with session.put(f"http://{gateway.host}/home/shades/positions?ids=154",
                           json={"rail": 1}) as response:
        assert response.status == 400
    async with session.put(f"http://{gateway.host}/home/shades/positions?ids=x") as response:
        assert response.status == 400


async def test_a_jog_asks_the_shade_to_show_itself_and_moves_nothing_known(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    await client.jog(154)
    assert [(r.path, r.body) for r in _commands(gateway)] == [
        ("/home/shades/154/motion", {"motion": "jog"})]
    assert gateway.shades[154].target is None


async def test_a_jog_the_gateway_reports_an_error_for_is_an_error(
    client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    gateway.command_errors[154] = 7
    with pytest.raises(CommandError, match="err=7"):
        await client.jog(154)


async def test_a_motion_for_a_shade_or_of_a_kind_not_known_is_refused(
    session: aiohttp.ClientSession, client: PowerViewClient, gateway: SimulatedGateway
) -> None:
    _furnish(gateway)
    with pytest.raises(ResponseError, match="HTTP 404"):
        await client.jog(1)
    async with session.put(f"http://{gateway.host}/home/shades/154/motion",
                           json={"motion": "heart"}) as response:
        assert response.status == 400
        assert await response.json() == {"errMsg": 'motion property must be one of ["jog"]'}
    async with session.put(f"http://{gateway.host}/home/shades/154/motion") as response:
        assert response.status == 400
