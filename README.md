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

`PowerViewHub` follows a whole home: it keeps every shade's and gateway's state current from
the gateway's event stream and sends commands. It works the same in any asyncio program - a
script, a service, or a home automation platform.

```python
import asyncio
import aiohttp
from powerview_connect import PowerViewHub, ShadeChanged

async def main():
    async with aiohttp.ClientSession() as session:
        async with PowerViewHub(session, "<primary-gateway-ip>") as hub:
            for state in hub.shades.values():
                print(state.id, state.shade.name, dict(state.positions))

            top, bottom = hub.shade(154).covers           # a top-down/bottom-up shade
            await hub.move_cover(154, top, 0.5)           # moves the top rail only

            async for change in hub.changes():
                if isinstance(change, ShadeChanged) and not change.state.moving:
                    print(change.state.id, "stopped at", dict(change.state.positions))

asyncio.run(main())
```

Use the address of the **primary** gateway: it lists the whole home, auxiliary gateways and
their shades included.

### Running it

| Call | Does |
|---|---|
| `await hub.load()` | reads the home once; raises `CannotConnectError` when the gateway does not answer |
| `await hub.run()` | follows the home until cancelled: the event stream, reads of moving shades, a read of the whole home every 10 minutes, and each gateway's own state every minute |
| `async with PowerViewHub(...)` | `load`, then `run` in a task of the hub's own, cancelled on exit |

A program with its own task management - Home Assistant, for one - calls `load` and runs `run`
as a task it owns, so stopping that task stops everything the hub does. Commands work once the
home is loaded, whether or not `run` runs.

The gateway not answering is not an error to `run`: `hub.connection` says so (`CONNECTED`,
`STREAM_DOWN` while the stream is reopened, `UNREACHABLE`), `hub.available` is false while it
does not answer, and the hub keeps trying. The stream is silent while nothing moves; the hub
reopens a silent stream without the gateway going unavailable.

### What is known

| | |
|---|---|
| `hub.shade(id)`, `hub.shades` | a `ShadeState`: the `Shade` as last read (name, room, capabilities, signal strength, battery), `positions`, `targets`, `directions`, `moving`, `online`, `eta`, and its `covers` |
| `hub.gateways` | a `GatewayState` per gateway: whether it is `askable`, whether it was `reachable` when last asked, and its last `status` |
| `hub.gateway_of(id)` | the gateway that serves a shade |
| `hub.home` | the home as last read: gateways, shades and scenes |
| `hub.snapshot()` | what a bug report needs, without addresses, serial numbers or Bluetooth names |

A shade, scene or gateway missing from one read of the home is kept until a second read in a
row misses it too, so one incomplete answer from a gateway that is starting up removes
nothing.

### Changes

`hub.subscribe(listener)` calls `listener` with every change; `subscribe_shade(id, listener)`
and `subscribe_gateway(serial, listener)` with one shade's or gateway's state. Each returns
the function that stops it. Listeners are called in the event loop, only when something
changed; one that raises is logged and the others are still called. `hub.changes()` yields the
same changes for an `async for`.

| Change | When |
|---|---|
| `ShadeChanged` | a shade's state changed: it moved, started or stopped, went offline, or reported a new signal strength or battery status |
| `GatewayChanged` | a gateway answered or stopped answering, or reported another state |
| `ConnectionChanged` | the hub's connection to the primary gateway changed |
| `HomeChanged` | shades, scenes or gateways were added, removed or changed; its `diff` says which |

### Covers

`covers(shade)` - also `ShadeState.covers` - says how a shade is controlled: a `CoverSpec` for
each of its covers, by its capabilities. A top-down/bottom-up shade has `TOP` and `BOTTOM`, a
dual-overlapped one `FRONT` and `REAR`, a tilt-only shade `VANES`, and others one `SHADE`. Each
says which rail it moves, whether it also tilts the vanes, and whether its rail's 0.0 is open
(a top-down shade). Its `openness`, `closed` and `direction` read a state's positions, with that
inversion applied, as 0.0 closed to 1.0 open; `hub.move_cover` and `hub.tilt_cover` move it by
openness. `capability_name` names a shade's capabilities.

### Commands

| Method | Does |
|---|---|
| `hub.move(id, {Rail.PRIMARY: 0.2})` | moves only the rails given; the shade keeps the others |
| `hub.move_cover(id, cover, openness)`, `hub.tilt_cover(id, cover, openness)` | moves or tilts one cover |
| `hub.stop(id)` | stops the shade, every rail, as the gateway stops a whole shade |
| `hub.jog(id)` | a short move back and forth, to show which shade it is |
| `hub.activate_scene(id)` | a scene stored on the gateway |
| `hub.refresh()`, `hub.refresh_gateways()` | reads the home, or asks each gateway of its state, now |

A shade or scene the home does not have raises `KeyError` before anything is sent. A command
the gateway reports an error for raises `CommandError`, whose `failures` name each shade by
its id and the gateway's error code. A gateway that does not answer raises
`CannotConnectError`.

### Timing, clock and logging

`PowerViewHub(session, host, timing=Timing(...), clock=..., logger=...)`:

- **`Timing`** holds every interval and limit: how often a moving shade is read
  (`poll_interval`, 1 s), the home (`refresh_interval`, 10 min) and each gateway
  (`gateway_interval`, 1 min); how many moving shades are read at once
  (`max_concurrent_reads`, 3); the waits before reopening the stream; and when a move without
  a stop event has ended.
- **`clock`** is anything with `monotonic()` and `async sleep(seconds)`; `SystemClock` by
  default, `testing.ManualClock` in tests.
- **The logger** is `powerview_connect` by default. At debug level it logs every event and
  every request with how long it took. It names shades and gateways by id and name, never by
  serial number or Bluetooth name.

## The client underneath

`PowerViewClient` sends one request at a time, for a program that wants to do the following
itself:

```python
client = PowerViewClient(session, "<gateway-ip>")
home = await client.get_home()                     # gateways, shades, scenes
await client.set_positions([154], secondary=0.5)   # the top rail only
async with client.shade_events(idle_timeout=360) as events:
    async for event in events:
        print(event.kind, event.shade_id, dict(event.current))
```

`home.gateway_at(host)` says which gateway an address is, and `home.gateway_of(id)` which
gateway serves a shade.

### What a gateway reports

Each `Gateway` of the home carries its name, `ip`, `model`, `firmware`, `radio_firmware`,
whether it `is_primary`, and `shade_ids`: the shades it serves. Every gateway, auxiliary ones
too, answers `get_gateway()` on a client of its own address with a `GatewayStatus`: its
`brand`, `hardware_version`, `firmware`, the network `interface` in use (`wifi` says whether it
is on Wi-Fi) and `internet_connected`. A gateway that does not answer raises
`CannotConnectError`.

```python
for gateway in home.gateways:
    status = await PowerViewClient(session, gateway.ip, read_only=True).get_gateway()
    print(gateway.name, status.firmware, status.internet_connected, gateway.shade_ids)
```

### What a shade reports

A `Shade` carries its name, room, `capabilities` (named by `capability`), `type`, `power_type`
(1 is hard-wired), `firmware`, `positions`, and `signal_strength`: how well the gateway hears
it, in dBm, closer to 0 being stronger. `battery` names the battery status (`BatteryStatus`); a
hard-wired shade reports one too, so it means something only where `has_battery` is true;
`battery_low` says whether that battery is low.

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
| `jog(shade_id)` | a short move back and forth, to show which shade it is |

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

The stream ends when the gateway closes it, raises `CannotConnectError` when it breaks, and
`StreamIdleError`, a kind of `CannotConnectError`, when it sends nothing for `idle_timeout`
seconds; the gateway sends nothing while nothing moves, so reopen it. `eta` is the gateway's estimate of the move's time, which real moves took about
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

`SimulatedGateway(primary=False)` answers as an auxiliary gateway; list one in a home with
`add_gateway(serial, name, ip=auxiliary.host)`, and give a shade to it with
`add_shade(..., gateway=serial)`. `interface` and `internet_state` set what `/gateway` reports.

`refuse_streams` answers the event stream with HTTP 503, `break_streams()` cuts open streams
off mid-line, and `shade_read_delay` slows each read of a shade down, as on a busy gateway. A
hub's tests take a `testing.ManualClock`, which moves only on `await clock.advance(seconds)`.

It also drops streams (`drop_streams`), goes offline and comes back (`close`, `start`),
reports a shade offline (`set_online`) and reports errors for commands (`command_errors`).

## Links

- [Releases](https://github.com/HairingX/powerview_connect/releases)
- [Issues](https://github.com/HairingX/powerview_connect/issues)
- [Home Assistant integration](https://github.com/HairingX/ha_powerview_connect)
