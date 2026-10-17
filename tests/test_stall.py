"""Raising a cover that stopped short of fully open, whoever started its move."""
import asyncio
import logging
from collections.abc import AsyncIterator
from typing import cast

import aiohttp
import pytest

from conftest import until
from powerview_connect import (
    Capabilities,
    Change,
    CommandError,
    Connection,
    CoverRole,
    PowerViewHub,
    Rail,
    ShadeStalled,
    StallOutcome,
    StallRecovery,
    Timing,
)
from powerview_connect._client import PowerViewClient
from powerview_connect._model import EventKind
from powerview_connect.testing import ManualClock, SimulatedGateway

P, S, T = Rail.PRIMARY, Rail.SECONDARY, Rail.TILT
CURTAIN, TDBU = 271, 154
TIMING = Timing(stream_idle_timeout=30.0, min_timeout=90.0)
RECOVERY = StallRecovery(min_openness=0.8, steps=3, pause=10.0)


def _furnish(gateway: SimulatedGateway) -> None:
    gateway.add_room(65, "Living Room")
    gateway.add_shade(CURTAIN, "Patio Curtain", room_id=65)
    gateway.add_shade(TDBU, "Kitchen Window", room_id=65,
                      capabilities=Capabilities.TOP_DOWN_BOTTOM_UP, shade_type=8)


def _moves(gateway: SimulatedGateway) -> list[object]:
    return [request.body for request in gateway.requests
            if request.method == "PUT" and request.path == "/home/shades/positions"]


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def app(session: aiohttp.ClientSession, gateway: SimulatedGateway) -> PowerViewClient:
    """The PowerView app, moving shades as a user of it does."""
    return PowerViewClient(session, gateway.host)


async def _running(hub: PowerViewHub) -> asyncio.Task[None]:
    await hub.load()
    task = asyncio.create_task(hub.run())
    await until(lambda: hub.connection is Connection.CONNECTED)
    return task


async def _stop(task: asyncio.Task[None]) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.fixture
async def hub(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, clock: ManualClock
) -> AsyncIterator[PowerViewHub]:
    """A running hub of the furnished home that raises covers which stop short."""
    _furnish(gateway)
    found = PowerViewHub(session, gateway.host, timing=TIMING, clock=clock,
                         stall_recovery=RECOVERY)
    task = await _running(found)
    yield found
    await _stop(task)


@pytest.fixture
def stalls(hub: PowerViewHub) -> list[ShadeStalled]:
    found: list[ShadeStalled] = []

    def note(change: Change) -> None:
        if isinstance(change, ShadeStalled):
            found.append(change)

    hub.subscribe(note)
    return found


async def _stalled_at(app: PowerViewClient, gateway: SimulatedGateway, hub: PowerViewHub,
                      shade_id: int, at: float) -> None:
    """The app opens the shade fully, and it stops at `at`."""
    await app.set_positions([shade_id], primary=1.0)
    await until(lambda: hub.shade(shade_id).moving)
    gateway.finish(shade_id, at={P: at})


async def _pause(clock: ManualClock, seconds: float = 10.0) -> None:
    for _ in range(round(seconds / 0.5)):
        await clock.advance(0.5)
        await asyncio.sleep(0.01)


async def _step(hub: PowerViewHub, gateway: SimulatedGateway, moves: int,
                at: float | None = None, shade_id: int = CURTAIN) -> None:
    """Wait for the hub's next step, the `moves`th move sent, and end it, at `at` if given,
    until the hub has taken the stop."""
    await until(lambda: len(_moves(gateway)) == moves and hub.shade(shade_id).moving)
    gateway.finish(shade_id, at=None if at is None else {P: at})
    await until(lambda: not hub.shade(shade_id).moving)
    await asyncio.sleep(0.02)


def _rails_sent(gateway: SimulatedGateway) -> list[set[str]]:
    found: list[set[str]] = []
    for body in _moves(gateway):
        positions = cast(dict[str, object], body).get("positions") if isinstance(body, dict) else None
        found.append(set(cast(dict[str, object], positions)) if isinstance(positions, dict) else set())
    return found


async def test_a_curtain_the_app_opened_that_stopped_short_is_raised_in_steps(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: len(stalls) == 1)
    assert stalls[0] == ShadeStalled(CURTAIN, CoverRole.SHADE, 0.9, StallOutcome.RECOVERING)
    for moves in (2, 3, 4):
        await _step(hub, gateway, moves)
        await _pause(clock)
    await until(lambda: len(stalls) == 2)
    assert stalls[1] == ShadeStalled(CURTAIN, CoverRole.SHADE, 1.0, StallOutcome.RECOVERED)
    assert _moves(gateway)[1:] == [{"positions": {"primary": value}} for value in (0.93, 0.97, 1.0)]
    assert gateway.requests[-1].query == {"ids": str(CURTAIN)}


async def test_the_bottom_rail_is_raised_with_only_its_own_rail(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, TDBU, 0.85)
    for moves in (2, 3, 4):
        await _step(hub, gateway, moves, shade_id=TDBU)
        await _pause(clock)
    await until(lambda: len(stalls) == 2)
    assert [stall.role for stall in stalls] == [CoverRole.BOTTOM, CoverRole.BOTTOM]
    assert stalls[1].outcome is StallOutcome.RECOVERED
    assert _rails_sent(gateway) == [{"primary"}] * 4


async def test_a_step_that_does_not_raise_the_curtain_gives_up_with_no_more_steps(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await _step(hub, gateway, 2, at=0.9)
    await _pause(clock)
    await until(lambda: len(stalls) == 2)
    assert stalls[1] == ShadeStalled(CURTAIN, CoverRole.SHADE, 0.9, StallOutcome.GAVE_UP)
    await _pause(clock, 30.0)
    assert len(_moves(gateway)) == 2


async def test_a_step_without_motion_stopped_in_time_gives_up(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: len(_moves(gateway)) == 2)
    gateway.move(CURTAIN, {P: 0.92})
    await _pause(clock, 95.0)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is StallOutcome.GAVE_UP
    assert len(_moves(gateway)) == 2


async def test_the_recovery_own_stops_do_not_start_another_recovery(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await _step(hub, gateway, 2, at=0.95)
    await _pause(clock)
    await _step(hub, gateway, 3, at=0.96)
    await _pause(clock)
    await _step(hub, gateway, 4, at=0.97)
    await _pause(clock)
    await until(lambda: len(stalls) == 2)
    assert stalls[1] == ShadeStalled(CURTAIN, CoverRole.SHADE, 0.97, StallOutcome.GAVE_UP)
    gateway.emit(EventKind.MOTION_STOPPED, CURTAIN)
    await _pause(clock, 30.0)
    assert len(stalls) == 2 and len(_moves(gateway)) == 4


async def test_a_stop_event_during_the_pause_does_not_end_it_early(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await _step(hub, gateway, 2)
    gateway.emit(EventKind.MOTION_STOPPED, CURTAIN)
    await _pause(clock, 9.0)
    assert len(_moves(gateway)) == 2
    await _pause(clock, 1.0)
    await until(lambda: len(_moves(gateway)) == 3)
    assert len(stalls) == 1


@pytest.mark.parametrize("command", ["move", "move_cover", "stop", "jog"])
async def test_a_command_of_the_caller_during_a_recovery_interrupts_it(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled], command: str,
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: len(_moves(gateway)) == 2)
    match command:
        case "move":
            await hub.move(CURTAIN, {P: 0.5})
        case "move_cover":
            await hub.move_cover(CURTAIN, hub.shade(CURTAIN).covers[0], 0.5)
        case "stop":
            await hub.stop(CURTAIN)
        case _:
            await hub.jog(CURTAIN)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is StallOutcome.INTERRUPTED
    sent = len(_moves(gateway))
    gateway.finish(CURTAIN)
    await _pause(clock, 30.0)
    assert len(_moves(gateway)) == sent


async def test_a_move_from_the_app_during_a_recovery_interrupts_it(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await _step(hub, gateway, 2)
    await app.set_positions([CURTAIN], primary=0.2)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is StallOutcome.INTERRUPTED
    await _pause(clock, 30.0)
    assert len(_moves(gateway)) == 3


async def test_a_shade_going_offline_during_a_recovery_interrupts_it(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: len(_moves(gateway)) == 2)
    gateway.set_online(CURTAIN, False)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is StallOutcome.INTERRUPTED


async def test_a_shade_removed_from_the_home_during_a_recovery_interrupts_it(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: len(_moves(gateway)) == 2)
    gateway.remove_shade(CURTAIN)
    await hub.refresh()
    await hub.refresh()
    await until(lambda: len(stalls) == 2)
    assert stalls[1] == ShadeStalled(CURTAIN, CoverRole.SHADE, None, StallOutcome.INTERRUPTED)


async def test_a_step_the_gateway_refuses_gives_up(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, stalls: list[ShadeStalled],
) -> None:
    await app.set_positions([CURTAIN], primary=1.0)
    await until(lambda: hub.shade(CURTAIN).moving)
    gateway.command_errors[CURTAIN] = 7
    gateway.finish(CURTAIN, at={P: 0.9})
    await until(lambda: len(stalls) == 2)
    with pytest.raises(CommandError):
        await app.set_positions([CURTAIN], primary=1.0)
    assert stalls[1].outcome is StallOutcome.GAVE_UP


async def test_an_unexpected_error_in_a_recovery_is_logged_and_the_hub_goes_on(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient,
    stalls: list[ShadeStalled], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail(*_: object) -> None:
        raise RuntimeError("broken")

    monkeypatch.setattr(PowerViewClient, "set_positions", fail)
    await _stalled_at_without_moving(gateway, hub, CURTAIN, 0.9)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is StallOutcome.GAVE_UP
    assert "Raising shade 271 failed" in caplog.text
    gateway.set_online(TDBU, False)
    await until(lambda: not hub.shade(TDBU).online)


async def _stalled_at_without_moving(gateway: SimulatedGateway, hub: PowerViewHub,
                                     shade_id: int, at: float) -> None:
    """A move to fully open announced on the stream, as a voice assistant's, that stops at `at`."""
    gateway.emit(EventKind.MOTION_STARTED, shade_id,
                 targetPositions={"primary": 1.0, "secondary": 0.0, "tilt": 0.0, "etaInSeconds": 20})
    await until(lambda: hub.shade(shade_id).moving)
    gateway.finish(shade_id, at={P: at})


async def test_a_curtain_moved_by_the_hub_itself_is_raised_too(
    hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock, stalls: list[ShadeStalled],
) -> None:
    await hub.move(CURTAIN, {P: 1.0})
    await until(lambda: gateway.shades[CURTAIN].target is not None)
    gateway.finish(CURTAIN, at={P: 0.9})
    await until(lambda: len(stalls) == 1)
    assert stalls[0].outcome is StallOutcome.RECOVERING


async def test_a_move_ended_by_the_tracker_without_an_event_never_starts_a_recovery(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await app.set_positions([CURTAIN], primary=1.0)
    await until(lambda: hub.shade(CURTAIN).moving)
    gateway.move(CURTAIN, {P: 0.9})
    await _pause(clock, 10.0)
    await until(lambda: not hub.shade(CURTAIN).moving)
    await _pause(clock, 30.0)
    assert stalls == [] and len(_moves(gateway)) == 1


async def test_a_stop_sent_before_the_curtain_stopped_is_no_stall(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, stalls: list[ShadeStalled],
) -> None:
    await app.set_positions([CURTAIN], primary=1.0)
    await until(lambda: hub.shade(CURTAIN).moving)
    await hub.stop(CURTAIN)
    gateway.finish(CURTAIN, at={P: 0.9})
    await until(lambda: not hub.shade(CURTAIN).moving)
    assert stalls == []


async def _hub_stalls(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient
) -> list[Change]:
    changes: list[Change] = []
    hub.subscribe(changes.append)
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: hub.shade(CURTAIN).positions[P] == 0.9)
    await asyncio.sleep(0.05)
    return changes


async def test_recovery_is_off_by_default(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, app: PowerViewClient,
    clock: ManualClock,
) -> None:
    _furnish(gateway)
    hub = PowerViewHub(session, gateway.host, timing=TIMING, clock=clock)
    task = await _running(hub)
    changes = await _hub_stalls(hub, gateway, app)
    assert not any(isinstance(change, ShadeStalled) for change in changes)
    assert len(_moves(gateway)) == 1
    await _stop(task)


async def test_a_read_only_hub_never_raises_a_curtain(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, app: PowerViewClient,
    clock: ManualClock,
) -> None:
    _furnish(gateway)
    hub = PowerViewHub(session, gateway.host, timing=TIMING, clock=clock, read_only=True,
                       stall_recovery=RECOVERY)
    task = await _running(hub)
    changes = await _hub_stalls(hub, gateway, app)
    assert not any(isinstance(change, ShadeStalled) for change in changes)
    await _stop(task)


async def test_nothing_is_raised_while_run_does_not_run(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, clock: ManualClock,
) -> None:
    _furnish(gateway)
    hub = PowerViewHub(session, gateway.host, timing=TIMING, clock=clock, stall_recovery=RECOVERY)
    task = await _running(hub)
    await _stop(task)
    changes: list[Change] = []
    hub.subscribe(changes.append)
    await hub.move(CURTAIN, {P: 1.0})
    gateway.finish(CURTAIN, at={P: 0.9})
    await asyncio.sleep(0.05)
    assert not any(isinstance(change, ShadeStalled) for change in changes)
    assert len(_moves(gateway)) == 1


async def test_a_recovery_ends_interrupted_when_run_is_cancelled(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, app: PowerViewClient,
    clock: ManualClock, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="powerview_connect")
    _furnish(gateway)
    hub = PowerViewHub(session, gateway.host, timing=TIMING, clock=clock, stall_recovery=RECOVERY)
    task = await _running(hub)
    changes: list[Change] = []
    hub.subscribe(changes.append)
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: len(_moves(gateway)) == 2)
    await _stop(task)
    stalls = [change for change in changes if isinstance(change, ShadeStalled)]
    assert [stall.outcome for stall in stalls] == [StallOutcome.RECOVERING,
                                                   StallOutcome.INTERRUPTED]
    assert "Shade 271 (Patio Curtain) stopped at 0.90" in caplog.text
    assert "SIM:" not in caplog.text
