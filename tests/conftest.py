"""Shared test setup: a simulated gateway and a client of it, and the live tests' settings -
`live_or_skip` skips those, and the `live` marker excludes them via `pytest -m "not live"`."""
import importlib
import os
from collections.abc import AsyncIterator

import aiohttp
import pytest

from powerview_connect import PowerViewClient
from powerview_connect.testing import SimulatedGateway


def live_setting(name: str) -> str | None:
    """A live test's setting: environment first, then mysecrets.py (gitignored)."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        mysecrets = importlib.import_module("mysecrets")
    except ImportError:
        return None
    found: object = getattr(mysecrets, name, None)
    return str(found) if found else None


def live_or_skip(what: str, **settings: str | None) -> list[pytest.MarkDecorator]:
    """Marks for a module of live tests: the `live` marker, plus a skip naming what is missing."""
    missing = [name for name, value in settings.items() if not value]
    return [
        pytest.mark.live,
        pytest.mark.skipif(
            bool(missing),
            reason=f"No {what} configured. Set {', '.join(missing)} as environment "
                   f"variables or in a gitignored mysecrets file.",
        ),
    ]


@pytest.fixture
async def gateway() -> AsyncIterator[SimulatedGateway]:
    """A started simulated gateway with no shades yet."""
    async with SimulatedGateway() as simulated:
        yield simulated


@pytest.fixture
async def session() -> AsyncIterator[aiohttp.ClientSession]:
    async with aiohttp.ClientSession() as opened:
        yield opened


@pytest.fixture
def client(session: aiohttp.ClientSession, gateway: SimulatedGateway) -> PowerViewClient:
    return PowerViewClient(session, gateway.host, timeout=2.0)
