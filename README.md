# PowerView Connect

An async Python client for **Hunter Douglas / Luxaflex PowerView Gen 3** gateways, over their
local HTTP API and shade event stream.

It is built on what a real Gen 3 gateway was measured to do (see
[the protocol notes](https://github.com/HairingX/powerview_connect/blob/main/docs/protocol.md)):

- **A move sends only the rails it names.** The gateway leaves a rail that is not sent where it
  is, so a command to the top rail of a top-down/bottom-up shade can never undo a command to the
  bottom rail sent a moment before.
- **The event stream says when a shade starts and stops moving**, whatever moved it: this
  client, the PowerView app, a scene, a voice assistant. Nothing needs polling while nothing
  moves.
- **A moving shade reports its live position**, so a shade can be followed by polling it, and
  only it, while it moves. `ShadeTracker` decides when a move has ended.

aiohttp is its only dependency. It is unofficial: PowerView is a trademark of Hunter Douglas,
which has nothing to do with this library.

## Installation

```bash
pip install powerview-connect
```

## Usage

```python
import asyncio
import aiohttp
from powerview_connect import EventKind, PowerViewClient, Rail

async def main():
    async with aiohttp.ClientSession() as session:
        client = PowerViewClient(session, "<gateway-ip>")

        home = await client.get_home()                 # gateways, shades, scenes
        for shade in home.shades.values():
            print(shade.id, shade.name, shade.capability, dict(shade.positions))

        await client.set_positions([154], secondary=0.5)   # the top rail only
        await client.set_positions([154], primary=0.2)     # the bottom rail only

        async with client.shade_events(idle_timeout=360) as events:
            async for event in events:
                if event.kind == EventKind.MOTION_STOPPED:
                    print(event.shade_id, "stopped at", dict(event.current))

asyncio.run(main())
```

Use the address of the **primary** gateway: it lists the whole home, auxiliary gateways and
their shades included. `home.gateway_at(host)` says which gateway an address is.

### What a shade reports

A `Shade` carries its name, room, `capabilities` (named by `capability`), `type`, `power_type`
(1 is hard-wired), `firmware`, `positions`, and `signal_strength`: how well the gateway hears
it, in dBm, closer to 0 being stronger. `battery_status` is the gateway's number for the battery;
a hard-wired shade reports one too, so it says nothing by itself.

### Positions

Every rail runs from `0.0`, fully closed, to `1.0`, fully open:

| Rail | Is | 0.0 | 1.0 |
|---|---|---|---|
| `Rail.PRIMARY` | the bottom rail of a top-down/bottom-up shade, the only rail of most others | down | up |
| `Rail.SECONDARY` | the top rail of a top-down/bottom-up shade | up | down |
| `Rail.TILT` | the vanes of a shade that tilts | closed | open |

A top-down/bottom-up shade's own firmware stops a rail where it meets the other one, so a move
that would cross them is safe to send.

### Commands

| Method | Sends |
|---|---|
| `set_positions(ids, primary=, secondary=, tilt=, velocity=)` | only the rails given, to every shade in `ids`, in one request |
| `stop(ids)` | stop where they are |
| `activate_scene(scene_id)` | a scene stored on the gateway |

A command the gateway reports an error for raises `CommandError`. A client made with
`read_only=True` refuses every command with `ReadOnlyError` before anything is sent.

### Events

`shade_events(idle_timeout)` opens the event stream; iterating it yields a `ShadeEvent` for
each event, with `kind`, `shade_id`, `current` and, for a motion start, `target` and `eta`:

| `EventKind` | When |
|---|---|
| `MOTION_STARTED` | a move starts, from any source; the motor follows some seconds later |
| `MOTION_STOPPED` | a move ends, with the final positions |
| `BATTERY_ALERT` | right before every move, by hard-wired shades too |
| `SHADE_ONLINE`, `SHADE_OFFLINE` | a shade is reachable again, or no longer |

The stream ends when the gateway closes it, and raises `CannotConnectError` when it breaks or
sends nothing for `idle_timeout` seconds; the gateway sends nothing while nothing moves, so
reopen it. `eta` is the gateway's estimate of the move's time, which real moves took about
twice as long as.

### Following a move

`ShadeTracker` follows one shade. It does no I/O and reads no clock: give it the events,
the positions polled while the shade moves, and the commands sent, each with the time.

```python
tracker = ShadeTracker(shade.positions)
tracker.motion_started(event, now=time.monotonic())        # or tracker.command(now, {Rail.PRIMARY: 0.2})
while tracker.poll((await client.get_shade(shade.id)).positions, now=time.monotonic()):
    print(tracker.current[Rail.PRIMARY], tracker.direction(Rail.PRIMARY))
    await asyncio.sleep(1)
```

A move ends with whichever comes first: motion-stopped, the target reached and still for
1.5 s, no change for 6 s after the shade moved, no move at all 25 s after the command, or a
timeout.

## Testing your code

`powerview_connect.testing.SimulatedGateway` is a gateway on 127.0.0.1 that answers as a real
one was measured to, for your own tests:

```python
from powerview_connect import Capabilities, PowerViewClient, Rail
from powerview_connect.testing import SimulatedGateway

async with SimulatedGateway() as gateway:
    gateway.add_room(1, "Living Room")
    gateway.add_shade(10, "Window", room_id=1, capabilities=Capabilities.TOP_DOWN_BOTTOM_UP)
    client = PowerViewClient(session, gateway.host)
    await client.set_positions([10], primary=0.5)    # motion-started on every stream
    gateway.move(10, {Rail.PRIMARY: 0.2})            # part of the way, as a poll sees it
    gateway.finish(10)                               # at the target; motion-stopped
    print(gateway.requests[-1].body)                 # {'positions': {'primary': 0.5}}
```

It also drops streams (`drop_streams`), goes offline and comes back (`close`, `start`),
reports a shade offline (`set_online`) and reports errors for commands (`command_errors`).

## Links

- [Releases](https://github.com/HairingX/powerview_connect/releases)
- [Issues](https://github.com/HairingX/powerview_connect/issues)
- [Home Assistant integration](https://github.com/HairingX/ha_powerview_connect)
