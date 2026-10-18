"""Retrying an opening whose motor backed off short of the top, whoever started it: the cover is
lowered, then raised in steps worked out from where the motor backed off to."""
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
    ReadOnlyError,
    ShadeStalled,
    RetryOutcome,
    StalledOpenRetry,
    Timing,
)
from powerview_connect._client import PowerViewClient
from powerview_connect._model import EventKind
from powerview_connect.testing import ManualClock, SimulatedGateway

P, S, T = Rail.PRIMARY, Rail.SECONDARY, Rail.TILT
CURTAIN, TDBU = 271, 154
TIMING = Timing(stream_idle_timeout=30.0, min_timeout=90.0, retry_move_timeout=40.0)
RETRY = StalledOpenRetry(rounds=2, delay=10.0)


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
    found = PowerViewHub(session, gateway.host, timing=TIMING, clock=clock)
    task = await _running(found)
    found.set_stalled_open_retry(CURTAIN, CoverRole.SHADE, RETRY)
    found.set_stalled_open_retry(TDBU, CoverRole.BOTTOM, RETRY)
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


async def _end_move(hub: PowerViewHub, gateway: SimulatedGateway, moves: int,
                    at: float | None = None, shade_id: int = CURTAIN) -> None:
    """Wait for the hub's next move, the `moves`th move sent, and end it, at `at` if given,
    until the hub has taken the stop: it stands still, or the hub has sent its next move."""
    await until(lambda: len(_moves(gateway)) == moves and hub.shade(shade_id).moving)
    gateway.finish(shade_id, at=None if at is None else {P: at})
    await until(lambda: len(_moves(gateway)) > moves or not hub.shade(shade_id).moving)
    await asyncio.sleep(0.02)


async def _quiet(clock: ManualClock) -> None:
    """Let the shade stay quiet long enough for the retry to read where it stands."""
    await _pause(clock, 3.5)


async def _ends(hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock, moves: int,
                at: float | None = None, shade_id: int = CURTAIN) -> None:
    """End the hub's `moves`th move, at `at` if given, and let the shade stay quiet, and then the
    delay after a step that held pass."""
    await _end_move(hub, gateway, moves, at, shade_id)
    await _pause(clock, 3.5 if at is not None else 14.0)


async def _lowered(hub: PowerViewHub, gateway: SimulatedGateway, clock: ManualClock, moves: int,
                   shade_id: int = CURTAIN) -> None:
    """End the hub's lowering, the `moves`th move sent, and let the shade stay quiet."""
    await _end_move(hub, gateway, moves, shade_id=shade_id)
    await _quiet(clock)


def _sent(gateway: SimulatedGateway) -> list[object]:
    """The primary positions of every move sent after the first."""
    return [cast(dict[str, dict[str, object]], body)["positions"]["primary"]
            for body in _moves(gateway)[1:]]


def _rails_sent(gateway: SimulatedGateway) -> list[set[str]]:
    found: list[set[str]] = []
    for body in _moves(gateway):
        positions = cast(dict[str, object], body).get("positions") if isinstance(body, dict) else None
        found.append(set(cast(dict[str, object], positions)) if isinstance(positions, dict) else set())
    return found


async def test_a_curtain_the_app_opened_that_stopped_short_is_lowered_and_raised_in_steps(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: len(stalls) == 1)
    assert stalls[0] == ShadeStalled(shade_id=CURTAIN, role=CoverRole.SHADE,
                                     openness=0.9, outcome=RetryOutcome.STARTED)
    await _quiet(clock)
    await _lowered(hub, gateway, clock, 2)
    for moves in (3, 4, 5):
        await _ends(hub, gateway, clock, moves)
    await until(lambda: len(stalls) == 2)
    assert stalls[1] == ShadeStalled(shade_id=CURTAIN, role=CoverRole.SHADE,
                                     openness=1.0, outcome=RetryOutcome.OPENED)
    assert _sent(gateway) == [0.8, 0.97, 0.98, 1.0]
    assert [request.query for request in gateway.requests
            if request.method == "PUT"][-1] == {"ids": str(CURTAIN)}


async def test_a_curtain_whose_motor_backs_off_at_the_top_is_raised_from_where_it_backed_off_to(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await app.set_positions([CURTAIN], primary=1.0)
    await until(lambda: hub.shade(CURTAIN).moving)
    gateway.finish(CURTAIN)
    await until(lambda: not hub.shade(CURTAIN).moving)
    gateway.drift(CURTAIN, {P: 0.95})
    await until(lambda: len(stalls) == 1)
    assert stalls[0].openness == 0.95
    await _quiet(clock)
    await _lowered(hub, gateway, clock, 2)
    for moves in (3, 4, 5):
        await _ends(hub, gateway, clock, moves)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is RetryOutcome.OPENED
    assert _sent(gateway) == [0.8, 0.98, 0.99, 1.0]


async def test_a_motor_still_backing_off_when_it_stopped_short_is_waited_for(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.99)
    await until(lambda: len(stalls) == 1)
    await _pause(clock, 2.0)
    gateway.drift(CURTAIN, {P: 0.95})
    await _pause(clock, 2.5)
    assert len(_moves(gateway)) == 1
    await _pause(clock, 1.0)
    await _lowered(hub, gateway, clock, 2)
    for moves in (3, 4, 5):
        await _ends(hub, gateway, clock, moves)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is RetryOutcome.OPENED
    assert _sent(gateway) == [0.8, 0.98, 0.99, 1.0]


async def test_the_bottom_rail_is_lowered_and_raised_with_only_its_own_rail(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, TDBU, 0.85)
    await _quiet(clock)
    await _lowered(hub, gateway, clock, 2, shade_id=TDBU)
    for moves in (3, 4, 5):
        await _ends(hub, gateway, clock, moves, shade_id=TDBU)
    await until(lambda: len(stalls) == 2)
    assert [stall.role for stall in stalls] == [CoverRole.BOTTOM, CoverRole.BOTTOM]
    assert stalls[1].outcome is RetryOutcome.OPENED
    assert _rails_sent(gateway) == [{"primary"}] * 5
    assert _sent(gateway) == [0.8, 0.95, 0.97, 1.0]


async def test_a_step_the_motor_backs_off_from_starts_a_new_round_from_where_it_backed_off_to(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await _quiet(clock)
    await _lowered(hub, gateway, clock, 2)
    await _ends(hub, gateway, clock, 3, at=0.92)
    await _lowered(hub, gateway, clock, 4)
    for moves in (5, 6, 7):
        await _ends(hub, gateway, clock, moves)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is RetryOutcome.OPENED
    assert _sent(gateway) == [0.8, 0.97, 0.8, 0.97, 0.99, 1.0]


async def test_a_motor_that_backs_off_instead_of_moving_starts_a_new_round(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await _quiet(clock)
    await _end_move(hub, gateway, 2)
    gateway.backing_off.add(CURTAIN)
    await _quiet(clock)
    await until(lambda: len(_moves(gateway)) == 3)
    gateway.drift(CURTAIN, {P: 0.86})
    await _quiet(clock)
    await _lowered(hub, gateway, clock, 4)
    for moves in (5, 6, 7):
        await _ends(hub, gateway, clock, moves)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is RetryOutcome.OPENED
    assert _sent(gateway) == [0.8, 0.97, 0.8, 0.95, 0.98, 1.0]


async def test_a_cover_that_backed_off_below_where_it_is_lowered_to_is_only_raised(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await _quiet(clock)
    await _lowered(hub, gateway, clock, 2)
    await _ends(hub, gateway, clock, 3, at=0.78)
    for moves in (4, 5, 6):
        await _ends(hub, gateway, clock, moves)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is RetryOutcome.OPENED
    assert _sent(gateway) == [0.8, 0.97, 0.93, 0.96, 1.0]


async def test_a_motor_that_backs_off_in_every_round_gives_up(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled], caplog: pytest.LogCaptureFixture,
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await _quiet(clock)
    for moves in (2, 4):
        await _lowered(hub, gateway, clock, moves)
        await _ends(hub, gateway, clock, moves + 1, at=0.9)
    await until(lambda: len(stalls) == 2)
    assert stalls[1] == ShadeStalled(shade_id=CURTAIN, role=CoverRole.SHADE,
                                     openness=0.9, outcome=RetryOutcome.GAVE_UP)
    assert "backed off in each of 2 rounds" in caplog.text
    assert _sent(gateway) == [0.8, 0.97, 0.8, 0.97]
    gateway.emit(EventKind.MOTION_STOPPED, CURTAIN)
    await _pause(clock, 30.0)
    assert len(stalls) == 2 and len(_moves(gateway)) == 5


async def test_a_stop_event_during_a_delay_does_not_end_it_early(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await _quiet(clock)
    await _lowered(hub, gateway, clock, 2)
    await _end_move(hub, gateway, 3)
    await _pause(clock, 3.5)
    gateway.emit(EventKind.MOTION_STOPPED, CURTAIN)
    await _pause(clock, 9.0)
    assert len(_moves(gateway)) == 3
    await _pause(clock, 1.5)
    await until(lambda: len(_moves(gateway)) == 4)


async def test_a_step_without_motion_stopped_in_time_gives_up(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await _quiet(clock)
    await until(lambda: len(_moves(gateway)) == 2)
    await _pause(clock, 45.0)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is RetryOutcome.GAVE_UP
    assert len(_moves(gateway)) == 2


@pytest.mark.parametrize("command", ["move", "move_cover", "stop", "jog"])
async def test_a_command_of_the_caller_during_a_retry_interrupts_it(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled], command: str,
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: len(stalls) == 1)
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
    assert stalls[1].outcome is RetryOutcome.INTERRUPTED
    sent = len(_moves(gateway))
    gateway.finish(CURTAIN)
    await _pause(clock, 30.0)
    assert len(_moves(gateway)) == sent


async def test_a_move_from_the_app_during_a_retry_interrupts_it(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await _quiet(clock)
    await until(lambda: len(_moves(gateway)) == 2)
    await app.set_positions([CURTAIN], primary=0.2)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is RetryOutcome.INTERRUPTED
    await _pause(clock, 30.0)
    assert len(_moves(gateway)) == 3


async def test_a_shade_going_offline_during_a_retry_interrupts_it(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: len(stalls) == 1)
    gateway.set_online(CURTAIN, False)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is RetryOutcome.INTERRUPTED


async def test_a_shade_removed_from_the_home_during_a_retry_interrupts_it(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: len(stalls) == 1)
    gateway.remove_shade(CURTAIN)
    await hub.refresh()
    await hub.refresh()
    await until(lambda: len(stalls) == 2)
    assert stalls[1] == ShadeStalled(shade_id=CURTAIN, role=CoverRole.SHADE,
                                     openness=None, outcome=RetryOutcome.INTERRUPTED)


async def test_a_step_the_gateway_refuses_gives_up(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await app.set_positions([CURTAIN], primary=1.0)
    await until(lambda: hub.shade(CURTAIN).moving)
    gateway.command_errors[CURTAIN] = 7
    gateway.finish(CURTAIN, at={P: 0.9})
    await _quiet(clock)
    await until(lambda: len(stalls) == 2)
    with pytest.raises(CommandError):
        await app.set_positions([CURTAIN], primary=1.0)
    assert stalls[1].outcome is RetryOutcome.GAVE_UP


async def test_an_unexpected_error_in_a_retry_is_logged_and_the_hub_goes_on(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient,
    clock: ManualClock, stalls: list[ShadeStalled], monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def fail(*_: object) -> None:
        raise RuntimeError("broken")

    monkeypatch.setattr(PowerViewClient, "set_positions", fail)
    await _stalled_at_without_moving(gateway, hub, CURTAIN, 0.9)
    await _quiet(clock)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is RetryOutcome.GAVE_UP
    assert "Retrying shade 271 failed" in caplog.text
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
    assert stalls[0].outcome is RetryOutcome.STARTED


async def test_a_move_ended_by_the_tracker_without_an_event_never_starts_a_retry(
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


async def test_retry_is_off_for_every_shade_until_turned_on(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, app: PowerViewClient,
    clock: ManualClock,
) -> None:
    _furnish(gateway)
    hub = PowerViewHub(session, gateway.host, timing=TIMING, clock=clock)
    task = await _running(hub)
    assert hub.stalled_open_retry(CURTAIN, CoverRole.SHADE) is None
    changes = await _hub_stalls(hub, gateway, app)
    assert not any(isinstance(change, ShadeStalled) for change in changes)
    assert len(_moves(gateway)) == 1
    await _stop(task)


async def test_a_read_only_hub_refuses_to_raise_a_curtain(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, clock: ManualClock,
) -> None:
    _furnish(gateway)
    hub = PowerViewHub(session, gateway.host, timing=TIMING, clock=clock, read_only=True)
    await hub.load()
    with pytest.raises(ReadOnlyError):
        hub.set_stalled_open_retry(CURTAIN, CoverRole.SHADE, RETRY)
    hub.set_stalled_open_retry(CURTAIN, CoverRole.SHADE, None)
    assert hub.stalled_open_retry(CURTAIN, CoverRole.SHADE) is None


async def test_turning_retry_on_for_a_shade_not_known_is_refused(hub: PowerViewHub) -> None:
    with pytest.raises(KeyError):
        hub.set_stalled_open_retry(1, CoverRole.SHADE, RETRY)


async def test_the_top_rail_of_a_top_down_bottom_up_shade_cannot_be_retried(
    hub: PowerViewHub,
) -> None:
    with pytest.raises(ValueError, match="top"):
        hub.set_stalled_open_retry(TDBU, CoverRole.TOP, RETRY)


async def test_turning_another_cover_retry_off_leaves_one_in_progress_running(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, TDBU, 0.9)
    await until(lambda: len(stalls) == 1)
    hub.set_stalled_open_retry(TDBU, CoverRole.TOP, None)
    await asyncio.sleep(0.05)
    assert [stall.outcome for stall in stalls] == [RetryOutcome.STARTED]
    assert hub.stalled_open_retry(TDBU, CoverRole.BOTTOM) == RETRY


async def test_a_shade_whose_fabric_does_not_gather_cannot_be_retried(
    session: aiohttp.ClientSession, gateway: SimulatedGateway,
) -> None:
    gateway.add_room(1, "Room")
    gateway.add_shade(5, "Roller", room_id=1, shade_type=1)
    hub = PowerViewHub(session, gateway.host)
    await hub.load()
    with pytest.raises(ValueError, match="shade 5"):
        hub.set_stalled_open_retry(5, CoverRole.SHADE, RETRY)


async def test_turning_retry_off_ends_one_in_progress_and_raises_no_more(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: len(stalls) == 1)
    hub.set_stalled_open_retry(CURTAIN, CoverRole.SHADE, None)
    await until(lambda: len(stalls) == 2)
    assert stalls[1].outcome is RetryOutcome.INTERRUPTED
    await _stalled_at(app, gateway, hub, CURTAIN, 0.85)
    await _pause(clock, 30.0)
    assert len(stalls) == 2 and len(_moves(gateway)) == 2


async def test_settings_changed_while_running_apply_to_the_next_stall(
    hub: PowerViewHub, gateway: SimulatedGateway, app: PowerViewClient, clock: ManualClock,
    stalls: list[ShadeStalled],
) -> None:
    settings = StalledOpenRetry(min_openness=0.95, lower_to=0.9, rounds=1, delay=0.0)
    hub.set_stalled_open_retry(CURTAIN, CoverRole.SHADE, settings)
    assert hub.stalled_open_retry(CURTAIN, CoverRole.SHADE) == settings
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await asyncio.sleep(0.05)
    assert stalls == []
    await _stalled_at(app, gateway, hub, CURTAIN, 0.96)
    await _quiet(clock)
    await _lowered(hub, gateway, clock, 3)
    await _ends(hub, gateway, clock, 4)
    await _ends(hub, gateway, clock, 5)
    await until(lambda: len(stalls) == 2)
    assert _sent(gateway)[-3:] == [0.9, 0.99, 1.0]
    assert stalls[1].outcome is RetryOutcome.OPENED


async def test_a_shade_removed_from_the_home_is_no_longer_raised(
    hub: PowerViewHub, gateway: SimulatedGateway,
) -> None:
    gateway.remove_shade(CURTAIN)
    await hub.refresh()
    await hub.refresh()
    assert hub.stalled_open_retry(CURTAIN, CoverRole.SHADE) is None


async def test_nothing_is_raised_while_run_does_not_run(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, clock: ManualClock,
) -> None:
    _furnish(gateway)
    hub = PowerViewHub(session, gateway.host, timing=TIMING, clock=clock)
    task = await _running(hub)
    hub.set_stalled_open_retry(CURTAIN, CoverRole.SHADE, RETRY)
    await _stop(task)
    changes: list[Change] = []
    hub.subscribe(changes.append)
    await hub.move(CURTAIN, {P: 1.0})
    gateway.finish(CURTAIN, at={P: 0.9})
    await asyncio.sleep(0.05)
    assert not any(isinstance(change, ShadeStalled) for change in changes)
    assert len(_moves(gateway)) == 1


async def test_a_retry_ends_interrupted_when_run_is_cancelled(
    session: aiohttp.ClientSession, gateway: SimulatedGateway, app: PowerViewClient,
    clock: ManualClock, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="powerview_connect")
    _furnish(gateway)
    hub = PowerViewHub(session, gateway.host, timing=TIMING, clock=clock)
    task = await _running(hub)
    hub.set_stalled_open_retry(CURTAIN, CoverRole.SHADE, RETRY)
    changes: list[Change] = []
    hub.subscribe(changes.append)
    await _stalled_at(app, gateway, hub, CURTAIN, 0.9)
    await until(lambda: any(isinstance(change, ShadeStalled) for change in changes))
    await _stop(task)
    stalls = [change for change in changes if isinstance(change, ShadeStalled)]
    assert [stall.outcome for stall in stalls] == [RetryOutcome.STARTED,
                                                   RetryOutcome.INTERRUPTED]
    assert "Shade 271 (Patio Curtain) stopped at 0.90" in caplog.text
    assert "SIM:" not in caplog.text
