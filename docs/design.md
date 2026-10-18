# Design

What the library does with what `protocol.md` says a real gateway does.

## Public and internal

The package's `__all__` is its whole public API, and the README documents nothing else:

- **The hub:** `PowerViewHub`, its `Timing`, the `Clock` it may be given, and the
  `StalledOpenRetry` a cover may be given, with its `RetryOutcome`.
- **What it knows:** `ShadeState`, `GatewayState` and `Connection`; the home as the gateway
  describes it: `Home`, `Shade`, `Scene`, `Gateway`, `GatewayStatus`, `Rail`, `Capabilities`,
  `BatteryStatus`; and the covers: `CoverSpec`, `CoverRole`.
- **Changes:** `Change`, `ShadeChanged`, `GatewayChanged`, `ConnectionChanged`, `HomeChanged`,
  `ShadeStalled`, `HomeDiff`.
- **Errors:** `PowerViewError` and its kinds, and `CommandFailure`.
- **`powerview_connect.testing`:** the simulated gateway, what it holds, and the manual clock.

It is built to grow by adding to it:

- **Every data class takes its fields by name** (`kw_only`), which also leaves it no positional
  `match` pattern: a field can be added anywhere, and the order of fields is no promise.
- **A setting names what it is for**, a retry its cover by shade id and role, so a second such
  cover on one shade needs no new signature.
- **Collections handed out are read-only:** `Mapping` copies of what the hub knows, and tuples
  for a home's gateways and scenes and a `CommandError`'s failures.

Everything else lives in modules whose names start with an underscore and may change in any
release: the HTTP client, the parsers, the event stream's events, the motion tracker. A program
that needs the gateway's protocol itself is better served by the hub, or by an issue asking for
what it lacks.

## Layers

| Module | Is | Does I/O | Keeps time |
|---|---|---|---|
| `_model.py` | the gateway's documents as dataclasses, and the parsers | no | no |
| `_covers.py` | which covers a shade is controlled as, by its capabilities | no | no |
| `_motion.py` | the tracker: whether one shade still moves | no | given the time |
| `_retry.py` | whether a cover's opening stalled, and how its retry goes | no | no |
| `_client.py` | one request at a time, and the event stream | yes | no |
| `_clock.py` | the `Clock` protocol, and the event loop's own clock | no | yes |
| `_hub.py` | `PowerViewHub`: a whole home, kept current while it runs | through the client | through its `Clock` |

Each module uses only those above it. The tests test each module on its own, internal ones
included, and the hub against the simulated gateway.

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

## Retrying an opening that stalls

The fabric of a pleated or cellular shade gathers as it opens, and can stop a shade sent fully
open short of the top. A motor that stalled was measured to refuse an opening sent from where it
stopped, and to back off downwards on the next command instead; sent up again in steps, it opened
fully. As the event stream says where every move aims and where
it stopped, whoever started it, the hub can tell such a stop and retry it so.

- **Opt-in, per cover.** It does something only for a cover it is turned on for, with that cover's
  own `StalledOpenRetry`, as only the user knows which shade stalls and how. A cover is named by its
  shade's id and its `role`, which stays the same when the shade is read again, as a shade may come
  to have more than one cover that can be retried. Turning it off for a cover interrupts a retry of
  it. Which covers are on is not kept: a host that persists settings keeps it.
- **Only on the `motion-stopped` that ends a move.** The tracker can end a move itself while the
  motor still runs, when nothing changed for `stall` seconds, and leave a stale position: that
  must not look like a stall, so the first `motion-stopped` after a move began still ends it. It
  keeps the last move's targets once the move ends, until the next one begins, and forgets them
  on a stop sent, as a stopped move did not aim where it ended. A motor that reached the top was
  measured to back off by itself a few seconds later, reporting where it is in `shade-online`
  events and ending with a `motion-stopped` of no move of its own: as the targets are kept, that
  counts as well.
- **Only covers that open upwards, of a shade whose fabric gathers** (`CoverSpec.retryable`). The
  movement comes from the capabilities: `SHADE` and `BOTTOM`. The `TOP` rail opens downwards and
  the shade's firmware stops it where it meets the bottom rail; a `BOTTOM` rail likewise stops
  at a lowered top rail, so it counts only while the top rail is at the top. Front, rear and
  vanes have not been measured. The fabric comes from the shade's `type`, which the gateway gives
  as a number for Hunter Douglas's own use: the pleated and cellular ones are 6, 7, 8, 9, 10, 33
  and 47, of which 6 and 8 were measured. A roller's fabric rolls up and does not gather.
- **A band.** The target must be fully open, and the stop from `min_openness` to just short of
  open: lower down, a stop is taken as meant, as from the app's stop button. Nearer the top, a
  stop from the app cannot be told from a stall; a shade often stopped there by hand is left
  with the retry off.
- **Wait, then raise in steps.** A step sent within seconds of the motor backing off was refused,
  so the retry first waits `wait`, 330 s by default. The rest of the way to the top is then split
  into steps of two thirds, five sixths and all of the way, as the motor was seen to back off on
  the last part of the way. Where a step ended counts once the shade has sent no event for
  `Timing.retry_settle`: a motor backing off reports where it is every 2 s, so a move sent while
  it still backs off would be lost in it. The motor was seen to take the first step after one or
  two refusals, so the first step is sent again at once, up to `attempts` times. After each step
  that held, the retry waits `delay` for the fabric to settle, whatever the shade sends
  meanwhile. A later step the motor backs off from starts a new round, waiting `wait` again, up to
  `rounds` in all. A motor that backs off sends no `motion-started`, so each move waits for the
  shade's next `motion-stopped` alone. Each start, step, back-off and end is logged as a warning,
  as the user tunes the timing from the log.
- **One retry per stall, one per shade**, as each move is a move of the shade. It is a task in
  `run`'s TaskGroup, so it ends with `run`; an exception in it is logged and ends it as `GAVE_UP`,
  never the TaskGroup. Each move is of the cover's rail alone, sent as `move` sends it but not
  taken as the caller's command; its target is set before it is sent, as its `motion-started` can
  come before the answer. The retry's own stops are not looked at for a stall, and the move's
  targets are forgotten once the retry ends, so each stall is retried once.
- **Interruptions.** A command of the caller, a `motion-started` aiming the rail anywhere but the
  move sent, the shade going offline or leaving the home, or the retry turned off ends it as
  `INTERRUPTED`. A `motion-started` without a target for the rail cannot be told from the
  retry's own, so it does not.
- **Time** is the hub's `Clock`: the wait for each move's stop, up to `Timing.retry_move_timeout`
  and apart from the tracker's own limits, the `wait` before each round, the quiet after each
  step, and the `delay` after each step that held, 300 s by default, as gathered fabric takes
  minutes to settle.

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
