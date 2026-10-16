# Design

What the library does with what `protocol.md` says a real gateway does.

## Layers

| Layer | Is | Does I/O | Keeps time |
|---|---|---|---|
| `_model.py` | the gateway's documents as dataclasses, and the parsers | no | no |
| `_covers.py` | which covers a shade is controlled as, by its capabilities | no | no |
| `_motion.py` | `ShadeTracker`: whether one shade still moves | no | given the time |
| `_client.py` | `PowerViewClient`: one request at a time, and the event stream | yes | no |
| `_hub.py` | `PowerViewHub`: a whole home, kept current while it runs | through the client | through its `Clock` |

Each layer uses only those above it, so a program can stop at any of them: parse captured
documents, drive a tracker from its own loop, or let the hub do everything.

## The hub

`run` is one coroutine holding an `asyncio.TaskGroup` of four tasks. Cancelling it cancels
them all, and nothing outlives it: a host that owns the task owns everything the hub does.

- **The event stream.** Opened, then the whole home read, to catch up on what happened while
  it was closed. A silent stream is how the gateway looks while nothing moves, so a
  `StreamIdleError` reopens it at once and changes nothing else. Any other end waits before the
  next attempt, longer each time; the waits start over once a stream has delivered an event or
  stayed open for `stream_healthy_after`.
- **Reads of moving shades.** One task, asleep while nothing moves. Every `poll_interval` it
  reads each moving shade, at most `max_concurrent_reads` at once; a shade that stopped, or left
  the home, while it waited for its turn is not read. A failed read still feeds the tracker, so
  its timeouts end a move the shade can no longer be asked about.
- **A read of the whole home** every `refresh_interval`.
- **Each gateway's own state** every `gateway_interval`, all at once. An auxiliary gateway is
  asked through a read-only client of the address the home lists for it, so nothing can reach
  a shade through it; a new address gets a new client.

## Connection

`reachable` is set by every request to the primary gateway: true when it answers, false when
it cannot be reached. With the stream's state it gives the `Connection`: `UNREACHABLE` while
the gateway does not answer, otherwise `CONNECTED` or `STREAM_DOWN`. Only `UNREACHABLE` makes
the hub unavailable, so a stream being reopened never hides the shades.

## State and changes

The hub keeps, per shade, the `Shade` as last read and its tracker, and turns them into a
frozen `ShadeState` after every input. A change is announced only when that state differs from
the last one announced. Listeners are called in the event loop, so a host that needs another
thread hands the change over itself.

A read of the home is compared with the one before. What a read misses is kept until
`removal_confirmations` reads in a row miss it, as one incomplete answer must not remove a
shade a user has named and placed. Shades whose name, room, capabilities, firmware or serving
gateway changed are reported as changed.

## Logging

Every log line and every error message names shades and gateways by id and name. Serial
numbers and Bluetooth names identify a person's devices, so they are left out; addresses are
kept, as they help find a fault.
