"""Read-only tests against a real PowerView Gen 3 gateway.

    Configure it either way:
        set POWERVIEW_HOST=<gateway-ip>        (environment variable)
        POWERVIEW_HOST = "<gateway-ip>"        (in mysecrets.py, which is gitignored)

    Optional:
        POWERVIEW_LISTEN   seconds to listen to the event stream, default 30; move a shade
                           with the PowerView app meanwhile to see its events

    Run:
        pytest tests/test_live_gateway.py -v -s

NOTHING HERE MOVES A SHADE: the client is read-only, and only GET /home and the event stream
are read.
"""
import asyncio
from collections.abc import AsyncIterator

import aiohttp
import pytest

from conftest import live_or_skip, live_setting
from powerview_connect import PowerViewClient, ReadOnlyError, ShadeEvent

HOST = live_setting("POWERVIEW_HOST")
LISTEN = float(live_setting("POWERVIEW_LISTEN") or "30")

pytestmark = live_or_skip("PowerView gateway", POWERVIEW_HOST=HOST)


@pytest.fixture
async def live() -> AsyncIterator[PowerViewClient]:
    assert HOST is not None   # guarded by the skip above
    async with aiohttp.ClientSession() as session:
        yield PowerViewClient(session, HOST, read_only=True)


async def test_a_command_is_refused_before_it_reaches_the_gateway(live: PowerViewClient) -> None:
    """The guard itself must work, or every other test here would be a risk."""
    with pytest.raises(ReadOnlyError):
        await live.stop([0])


async def test_the_home_is_read(live: PowerViewClient) -> None:
    home = await live.get_home()
    print("\n--- gateways ---")
    for gateway in home.gateways:
        print(f"  {'primary' if gateway.is_primary else 'auxiliary':9} {gateway.name!r} "
              f"model={gateway.model} firmware={gateway.firmware}")
    print("--- shades ---")
    for shade in home.shades.values():
        print(f"  {shade.id:>5} {shade.name!r:32} room={shade.room_name!r} "
              f"capabilities={shade.capabilities} type={shade.type} power={shade.power_type} "
              f"positions={ {str(rail): value for rail, value in shade.positions.items()} }")
    print(f"--- scenes --- {[scene.name for scene in home.scenes]}")
    assert home.gateway_at(live.host) is not None, "the gateway does not list itself"
    assert home.primary_gateway is not None
    assert all(0.0 <= value <= 1.0 for shade in home.shades.values()
               for value in shade.positions.values())


async def test_the_event_stream_opens_and_its_events_parse(live: PowerViewClient) -> None:
    home = await live.get_home()
    heard: list[ShadeEvent] = []
    async with live.shade_events(idle_timeout=LISTEN + 30) as events:
        async def listen() -> None:
            async for event in events:
                print(f"  {event.kind:16} shade {event.shade_id} current={dict(event.current)} "
                      f"target={dict(event.target)} eta={event.eta}")
                heard.append(event)

        try:
            await asyncio.wait_for(listen(), LISTEN)
        except TimeoutError:
            pass
    print(f"\n  {len(heard)} events in {LISTEN:.0f} s")
    assert all(event.shade_id in home.shades for event in heard)
