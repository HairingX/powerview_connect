# PowerView Connect

An async Python library for **Hunter Douglas / Luxaflex PowerView Gen 3** homes, over the
gateway's local HTTP API and shade event stream. It knows how PowerView works, so the program
using it does not have to: a script, a service, or a home automation platform.

It is built on what a real Gen 3 gateway was measured to do (see
[the protocol notes](https://github.com/HairingX/powerview_connect/blob/main/docs/protocol.md)):

- **A move sends only the rails it names.** The gateway leaves a rail that is not sent where it
  is, so a command to the top rail of a top-down/bottom-up shade can never undo a command to the
  bottom rail sent a moment before.
- **The event stream says when a shade starts and stops moving**, whatever moved it: this
  library, the PowerView app, a scene, a voice assistant. Nothing is read while nothing moves.
- **A moving shade reports its live position**, so it is read, and only it, while it moves.

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
| `ShadeStalled` | a cover stopped short of fully open and is being raised, or the raising ended; see below |

### Covers

`ShadeState.covers` says how a shade is controlled: a `CoverSpec` for each of its covers, by
its capabilities. A top-down/bottom-up shade has `TOP` and `BOTTOM`, a
dual-overlapped one `FRONT` and `REAR`, a tilt-only shade `VANES`, and others one `SHADE`. Each
says which rail it moves, whether it also tilts the vanes, and whether its rail's 0.0 is open
(a top-down shade). Its `openness`, `closed` and `direction` read a state's positions, with that
inversion applied, as 0.0 closed to 1.0 open; `hub.move_cover` and `hub.tilt_cover` move it by
openness.

### Commands

| Method | Does |
|---|---|
| `hub.move(id, {Rail.PRIMARY: 0.2})` | moves only the rails given; the shade keeps the others |
| `hub.move_cover(id, cover, openness)`, `hub.tilt_cover(id, cover, openness)` | moves or tilts one cover |
| `hub.stop(id)` | stops the shade, every rail, as the gateway stops a whole shade |
| `hub.jog(id)` | a short move back and forth, to show which shade it is |
| `hub.activate_scene(id)` | a scene stored on the gateway |
| `hub.refresh()`, `hub.refresh_gateways()` | reads the home, or asks each gateway of its state, now |

A read-only hub (`PowerViewHub(..., read_only=True)`) follows the home and refuses every
command. See Errors for what a command raises.

### Retrying an opening that stalls

A pleated or cellular shade sent fully open sometimes does not stay there: its fabric gathers, the
motor meets too much resistance and backs off, on the way up or once it reached the top. Lowered a
little, and then raised to the top in small steps with time between them for the fabric to settle,
it opens fully. The hub does that for the covers it is turned on for, whoever started the move:
this hub, the PowerView app, a voice assistant or a scene.

```python
hub.set_stalled_open_retry(271, CoverRole.SHADE, StalledOpenRetry())  # off until turned on
hub.stalled_open_retry(271, CoverRole.SHADE)
# StalledOpenRetry(min_openness=0.8, lower_by=0.1, steps=3, delay=90.0)
hub.set_stalled_open_retry(271, CoverRole.SHADE, StalledOpenRetry(min_openness=0.9, delay=60.0))
hub.set_stalled_open_retry(271, CoverRole.SHADE, None)  # off; ends a retry in progress
```

It is set per cover, by the shade's id and the cover's `role`. `CoverSpec.retryable` says which
cover of a shade can be retried: one that opens upwards (a `SHADE` cover, or the `BOTTOM` rail of
a top-down/bottom-up shade) of a pleated or cellular shade, whose fabric gathers as it opens.
Any other cover is refused with `ValueError`, and a read-only hub refuses with `ReadOnlyError`.
The hub does not keep which covers it is on for across restarts: that is the host's to keep.

- **What counts.** A `motion-stopped` event for that cover after a move aimed fully open, from
  `min_openness` (0.8) to just short of open: the move's own stop, or the stop of a motor that
  backed off by itself once the move reached the top. For a bottom rail, only while the top rail
  is at the top. A stop below `min_openness` is taken as meant. A stop this hub sent is never
  counted, nor a move the hub saw end without an event; a stop from the PowerView app near the
  top cannot be told from a motor backing off.
- **What it does.** It lowers the cover by `lower_by` (0.1) and, once the shade has sent no event
  for `Timing.retry_settle` (3 s), raises it to fully open in `steps` (3) even steps, moving only
  that cover's rail; after each step it waits `delay` (90 s) for the fabric to settle, and checks
  that the cover rose.
- **What it says.** `ShadeStalled(shade_id=, role=, openness=, outcome=)` once as it starts
  (`STARTED`, where the cover stopped), and once as it ends, where the cover ended: `OPENED`,
  fully open; `GAVE_UP`, a step did not raise it, or a move did not stop within
  `Timing.retry_move_timeout` (90 s) or could not be sent; or `INTERRUPTED`, a command of yours,
  a move or stop from elsewhere, the shade going offline or leaving the home, turning it off, or
  `run` ending.

It works only while `run` runs, and each retry is a task of `run`'s own, so nothing outlives it.
Each stalled opening is retried once, one cover of a shade at a time: the retry's own stops are
not looked at again.

### Timing, clock and logging

`PowerViewHub(session, host, timing=Timing(...), clock=..., logger=...)`:

- **`Timing`** holds every interval and limit, each documented on its field: how often a moving
  shade is read (`poll_interval`, 1 s), the home (`refresh_interval`, 10 min) and each gateway
  (`gateway_interval`, 1 min); how many moving shades are read at once
  (`max_concurrent_reads`, 3); the waits before reopening the stream; when a move without a
  stop event has ended (`stall`, `settle`, `start_grace`, `min_timeout`); and how long a move
  of a retried opening may take (`retry_move_timeout`), and how long a lowered shade is left
  quiet before it is raised (`retry_settle`). It refuses values the hub cannot work
  with.
- **`clock`** is any `Clock`: something with `monotonic()` and `async sleep(seconds)`. The
  event loop's own time by default, `testing.ManualClock` in tests.
- **The logger** is `powerview_connect` by default. At debug level it logs every event and
  every request with how long it took. It names shades and gateways by id and name, never by
  serial number or Bluetooth name.

## The home

`hub.home` is a `Home`: its `gateways`, `shades` by id, and `scenes`. `home.primary_gateway` is
the gateway that is not an auxiliary one, `home.gateway_at(host)` the one at an address, and
`home.gateway_of(id)` the one that serves a shade.

### Gateways

A `Gateway` carries its `name`, `ip`, `model`, `firmware`, `radio_firmware`, whether it
`is_primary`, and `shade_ids`: the shades it serves. The `status` of its `GatewayState` is a
`GatewayStatus`, what the gateway reports of itself: its `brand`, `hardware_version`,
`firmware`, the network `interface` in use (`wifi` says whether it is on Wi-Fi) and
`internet_connected`. Auxiliary gateways are asked at their own address, read-only.

### Shades

A `Shade` carries its `name`, `room_name`, `capabilities` (named by `capability` and
`capability_name`), `type`, `power_type`, `firmware`, `positions`, and `signal_strength`: how
well the gateway hears it, in dBm, closer to 0 being stronger. `battery` names its battery
status (`BatteryStatus`). A hard-wired shade reports one too, so it means something only where
`has_battery` is true; `battery_low` says whether that battery is low.

### Positions

Every rail runs from `0.0`, fully closed, to `1.0`, fully open:

| Rail | Is | 0.0 | 1.0 |
|---|---|---|---|
| `Rail.PRIMARY` | the bottom rail of a top-down/bottom-up shade, the only rail of most others | down | up |
| `Rail.SECONDARY` | the top rail of a top-down/bottom-up shade | up | down |
| `Rail.TILT` | the vanes of a shade that tilts | closed | open |

A top-down/bottom-up shade's own firmware stops a rail where it meets the other one, so a move
that would cross them is safe to send. A cover's openness (see Covers) has every rail's
direction sorted out already.

## Errors

| Error | When |
|---|---|
| `CannotConnectError` | the gateway does not answer |
| `ResponseError` | it answered with something other than what was asked for |
| `CommandError` | it reported an error for a command; `failures` name each shade by id, with the gateway's code |
| `ReadOnlyError` | a read-only hub refused a command before sending it |

All are a `PowerViewError`. A shade or scene the home does not have raises `KeyError` before
anything is sent.

## Testing your code

`powerview_connect.testing.SimulatedGateway` is a gateway on 127.0.0.1 that answers as a real
one was measured to, and `ManualClock` a clock that moves only when the test advances it:

```python
from powerview_connect import Capabilities, PowerViewHub, Rail
from powerview_connect.testing import ManualClock, SimulatedGateway

clock = ManualClock()
async with SimulatedGateway() as gateway:
    gateway.add_room(1, "Living Room")
    gateway.add_shade(10, "Window", room_id=1, capabilities=Capabilities.TOP_DOWN_BOTTOM_UP)
    async with PowerViewHub(session, gateway.host, clock=clock) as hub:
        await hub.move(10, {Rail.PRIMARY: 0.5})      # motion-started on every stream
        gateway.move(10, {Rail.PRIMARY: 0.2})        # part of the way, as a read sees it
        await clock.advance(1)                       # the hub reads the moving shade
        gateway.finish(10)                           # at the target; motion-stopped
        # gateway.finish(10, at={Rail.PRIMARY: 0.9}) ends it short of the target instead
        print(gateway.requests[-1].body)
```

`SimulatedGateway(primary=False)` answers as an auxiliary gateway; list one in a home with
`add_gateway(serial, name, ip=auxiliary.host)`, and give a shade to it with
`add_shade(..., gateway=serial)`. `interface` and `internet_state` set what `/gateway` reports.

`refuse_streams` answers the event stream with HTTP 503, `break_streams()` cuts open streams
off mid-line, and `shade_read_delay` slows each read of a shade down, as on a busy gateway.

`drift(id, positions)` moves a shade without a `motion-started`, as a motor backing off by
itself does, and a shade in `backing_off` takes its next command without one, as a motor that
stalled backs off instead.

It also drops streams (`drop_streams`), goes offline and comes back (`close`, `start`),
reports a shade offline (`set_online`) and reports errors for commands (`command_errors`).
What it holds can be changed directly: `shades` (`SimulatedShade`), `scenes` (`SimulatedScene`)
and `gateways` (`ListedGateway`).

## Stability

What `powerview_connect` and `powerview_connect.testing` name in `__all__` is the public API;
everything else may change in any release.

- **Any release may add** a field, a keyword argument, a method, a kind of `Change`, a member
  of an enum, or an error that is a kind of one already raised. Every data class takes its
  fields by name, in construction and in `match`, so a new field breaks no caller. A listener
  or `match` over changes passes over the kinds it does not know, and one over an enum keeps a
  default case.
- **A breaking release** raises at least the minor version, and its notes say what broke.
- **Never promised:** what `snapshot()` holds, and what is logged.

## Links

- [Releases](https://github.com/HairingX/powerview_connect/releases)
- [Issues](https://github.com/HairingX/powerview_connect/issues)
- [Home Assistant integration](https://github.com/HairingX/ha_powerview_connect)
