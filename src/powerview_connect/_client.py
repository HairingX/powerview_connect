"""The gateway's local HTTP API and its shade event stream."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator, AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import cast

import aiohttp

from ._model import (
    CommandFailure,
    GatewayStatus,
    Home,
    Shade,
    ShadeEvent,
    command_errors,
    parse_event,
    parse_gateway,
    parse_home,
    parse_shade,
)

DEFAULT_TIMEOUT = 10.0
"""Seconds a request may take."""


class PowerViewError(Exception):
    """Something went wrong talking to the gateway."""


class CannotConnectError(PowerViewError):
    """The gateway could not be reached, or the connection to it broke."""


class ResponseError(PowerViewError):
    """The gateway answered, but not with what was asked for."""


class StreamIdleError(CannotConnectError):
    """The event stream stayed silent longer than allowed, as it does while nothing moves."""


class CommandError(PowerViewError):
    """The gateway took a command but reported an error for it."""

    def __init__(self, failures: list[CommandFailure]) -> None:
        """An error for what the gateway reported; its message names shades by their id."""
        super().__init__("; ".join(str(failure) for failure in failures))
        self.failures = failures

    @property
    def shade_ids(self) -> frozenset[int]:
        """The shades the gateway reported an error for."""
        return frozenset(
            failure.shade_id for failure in self.failures if failure.shade_id is not None)


class ReadOnlyError(PowerViewError):
    """A command was refused before it was sent, as the client is read-only."""


def _ids(shade_ids: Iterable[int]) -> str:
    ids = ",".join(str(shade_id) for shade_id in shade_ids)
    if not ids:
        raise ValueError("at least one shade id is needed")
    return ids


class PowerViewClient:
    """A client for one PowerView Gen 3 gateway, on a session its owner keeps.

    Endpoints, as the Gen 3 API documents them and a gateway was measured to answer:

    - `GET /home`: the whole home
    - `GET /gateway`: the gateway's own state; auxiliary gateways answer it too
    - `GET /home/shades/{id}`: one shade; its positions change live while it moves
    - `PUT /home/shades/positions?ids=...`: move; a rail not given is not changed
    - `PUT /home/shades/stop?ids=...`
    - `PUT /home/scenes/{id}/activate`
    - `PUT /home/shades/{id}/motion` with `{"motion": "jog"}`, the only motion it takes
    - `GET /home/shades/events?sse=true`: shade events, as server-sent events
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        read_only: bool = False,
    ) -> None:
        """A client for the gateway at `host`, an address or name with an optional port.

        Args:
            read_only: refuse every command before it reaches the gateway.
        """
        self._session = session
        self._host = host.strip().removeprefix("http://").rstrip("/")
        self._timeout = timeout
        self._read_only = read_only

    @property
    def host(self) -> str:
        """The gateway's address, as it is reached."""
        return self._host

    @property
    def read_only(self) -> bool:
        """Whether every command is refused before it reaches the gateway."""
        return self._read_only

    @property
    def base_url(self) -> str:
        """The URL every endpoint is under."""
        return f"http://{self._host}"

    async def _request(self, method: str, path: str, body: object = None) -> object:
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                json=body,
                timeout=aiohttp.ClientTimeout(total=self._timeout),
            ) as response:
                text = await response.text()
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise CannotConnectError(f"{method} {path}: {err!r}") from err
        if response.status >= 400:
            raise ResponseError(f"{method} {path}: HTTP {response.status} {text[:200]}")
        if not text:
            return None
        try:
            return cast(object, json.loads(text))
        except ValueError as err:
            raise ResponseError(f"{method} {path}: not JSON: {text[:200]}") from err

    async def _get_object(self, path: str) -> dict[str, object]:
        found = await self._request("GET", path)
        if not isinstance(found, dict):
            raise ResponseError(f"GET {path}: not an object")
        return cast(dict[str, object], found)

    async def _command(self, path: str, body: object = None) -> object:
        if self._read_only:
            raise ReadOnlyError(f"PUT {path} refused: the client is read-only")
        response = await self._request("PUT", path, body)
        if errors := command_errors(response):
            raise CommandError(errors)
        return response

    async def get_home(self) -> Home:
        """The whole home: gateways, shades with their positions, and scenes."""
        return parse_home(await self._get_object("/home"))

    async def get_gateway(self) -> GatewayStatus:
        """What the gateway reports of itself: its hardware, firmware and network."""
        return parse_gateway(await self._get_object("/gateway"))

    async def get_shade(self, shade_id: int) -> Shade:
        """One shade, with its positions as they are now, also while it moves."""
        return parse_shade(await self._get_object(f"/home/shades/{shade_id}"))

    async def set_positions(
        self,
        shade_ids: Iterable[int],
        *,
        primary: float | None = None,
        secondary: float | None = None,
        tilt: float | None = None,
        velocity: float | None = None,
    ) -> None:
        """Move shades: only the rails given are sent, and the shades keep the others.

        Raises:
            ValueError: no rail is given, or a value is outside 0.0-1.0.
        """
        positions = {
            name: value
            for name, value in (
                ("primary", primary),
                ("secondary", secondary),
                ("tilt", tilt),
                ("velocity", velocity),
            )
            if value is not None
        }
        if not positions:
            raise ValueError("no position given")
        if any(not 0.0 <= value <= 1.0 for value in positions.values()):
            raise ValueError(f"positions run from 0.0 to 1.0: {positions}")
        await self._command(f"/home/shades/positions?ids={_ids(shade_ids)}", {"positions": positions})

    async def stop(self, shade_ids: Iterable[int]) -> None:
        """Stop shades where they are."""
        await self._command(f"/home/shades/stop?ids={_ids(shade_ids)}")

    async def activate_scene(self, scene_id: int) -> None:
        """Activate a scene stored on the gateway; the gateway moves its shades."""
        await self._command(f"/home/scenes/{scene_id}/activate")

    async def jog(self, shade_id: int) -> None:
        """Move a shade briefly back and forth, to show which one it is."""
        await self._command(f"/home/shades/{shade_id}/motion", {"motion": "jog"})

    @asynccontextmanager
    async def shade_events(
        self, idle_timeout: float
    ) -> AsyncGenerator[AsyncIterator[ShadeEvent]]:
        """The gateway's shade events, from a stream opened on entry and closed on exit.

        The stream ends when the gateway closes it. The gateway sends nothing while nothing
        moves, so a stream silent for `idle_timeout` seconds is taken as broken.

        Raises:
            CannotConnectError: on entry, when the stream cannot be opened; while iterating,
                when it breaks.
            StreamIdleError: while iterating, when the stream stays silent too long.
            ResponseError: on entry, when the gateway refuses the stream.
        """
        path = "/home/shades/events?sse=true"
        try:
            response = await self._session.get(
                f"{self.base_url}{path}",
                headers={"Accept": "text/event-stream"},
                timeout=aiohttp.ClientTimeout(
                    total=None, sock_connect=self._timeout, sock_read=idle_timeout
                ),
            )
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise CannotConnectError(f"GET {path}: {err!r}") from err
        try:
            if response.status != 200:
                raise ResponseError(f"GET {path}: HTTP {response.status}")
            yield self._events(response)
        finally:
            response.close()

    async def _events(self, response: aiohttp.ClientResponse) -> AsyncIterator[ShadeEvent]:
        try:
            async for line in response.content:
                if (event := parse_event(line.decode("utf-8", "replace"))) is not None:
                    yield event
        except TimeoutError as err:
            raise StreamIdleError(f"shade events: silent too long: {err!r}") from err
        except (aiohttp.ClientError, OSError) as err:
            raise CannotConnectError(f"shade events: {err!r}") from err
