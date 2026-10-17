# The Gen 3 gateway's API, as far as it is known

The endpoints and documents this library uses, written here in its own words. Each statement says
how it is known:

- **measured**: seen on a real gateway; the captures in `captures/` hold what it sent.
- **described**: stated by Hunter Douglas's description of the gateway's API, version 1.10.0
  (OpenAPI 3.0.3), but not measured. That description is not kept in this repository; where it can
  be read is at the end.

Where the two disagree, the measurement is right.

## Reading

| Request | What it gives | Known |
|---|---|---|
| `GET /home` | The whole home: rooms with their shades, scenes, gateways, remotes, automations. The description marks it as internal to Hunter Douglas; `GET /home/shades`, `/home/rooms` and `/home/scenes` are its public parts. | measured |
| `GET /home/shades` | Every shade, as a list of the documents `GET /home/shades/{id}` gives. | described |
| `GET /home/shades/{id}` | One shade. While it moves, its `positions` change about once a second. | measured |
| `GET /gateway` | The gateway's own state: hardware, firmware, network. Auxiliary gateways answer it too. | measured |
| `GET /home/scenes`, `GET /home/rooms` | The scenes and the rooms. | described |

## A shade's document

| Field | Meaning | Known |
|---|---|---|
| `id` | The shade's number; it stays when a motor is replaced. | measured; the description says it stays |
| `type` | A number for the kind of product, for Hunter Douglas's own use. | measured |
| `name`, `ptName` | The name, Base64-encoded, and as text. | measured |
| `capabilities` | How the shade moves; see below. | measured for 0 and 7, described for the others |
| `powerType` | 0 battery, 1 hard-wired, 2 rechargeable. | measured for 1, described for the others |
| `batteryStatus` | A rough gauge of the battery, 0 to 3; the description gives no meaning for each number. Hard-wired shades report 3, or 1 for type 6. | measured for hard-wired shades |
| `signalStrength` | The shade's average reception, RSSI in dBm. | measured |
| `positions` | Each rail from 0.0, fully closed, to 1.0, fully open: `primary`, `secondary`, `tilt`, and `velocity`. | measured |
| `firmware` | `revision`, `subRevision`, `build`. | measured |
| `bleName`, `serialNumber` | The Bluetooth name and the serial number. | measured |

`capabilities`, as the description groups shades by how they move:

| Value | Moves | Rails | Known |
|---|---|---|---|
| 0 | up from the bottom | primary | measured |
| 1 | up from the bottom, vanes tilting 90° | primary, tilt | described |
| 2 | up from the bottom, vanes tilting 180° | primary, tilt | described |
| 3 | sideways | primary | described |
| 4 | sideways, vanes tilting 180° | primary, tilt | described |
| 5 | vanes tilting 180° only | tilt | described |
| 6 | down from the top | primary | described |
| 7 | from the bottom and from the top | primary is the bottom rail, secondary the top rail | measured |
| 8 | a front and a rear shade | primary, secondary | described |
| 9 | a front and a rear shade, the front's vanes tilting 90° | primary, secondary, tilt | described |
| 10 | a front and a rear shade, the front's vanes tilting 180° | primary, secondary, tilt | described |

Where a front and a rear shade share one motor, the description says the shade's own firmware
puts the front one where it must be when the rear one moves.

## Commands

| Request | What it does | Known |
|---|---|---|
| `PUT /home/shades/positions?ids=1,2` with `{"positions": {...}}` | Moves the shades. A rail left out of `positions` does not move. `velocity`, from 0.0 to 1.0, sets the speed; left out, the default. | measured; the description says the same of a rail left out |
| `PUT /home/shades/stop?ids=1,2` | Stops the shades. | measured |
| `PUT /home/shades/{id}/motion` with `{"motion": "jog"}` | The shade jogs, to show which one it is. Any other motion is refused with HTTP 400. | measured; the description lists only `jog` |
| `PUT /home/scenes/{id}/activate` | Moves the scene's shades; answers with their ids. | described |

A command's answer is `{"err": 0, "responses": [...]}`, one response per shade, with `id`,
`bleName`, and `hex` where the shade took it (measured) or `err` where it did not (described). The shades can
also be named by Bluetooth name, with `?shades=` instead of `?ids=`; the description prefers ids.
An answer of HTTP 503 means the gateway has no home yet (described).

## The event stream

`GET /home/shades/events?sse=true` opens a server-sent event stream: `100 HELO`, then a
`data: {json}` line for each event (measured). An event has `evt`, `isoDate`, `bleName`, `id` and
`currentPositions` (measured).

| `evt` | When | Known |
|---|---|---|
| `motion-started` | A shade starts to move, for every source of the move. It may carry `targetPositions`, with `etaInSeconds`; the description says it may also come without. | measured |
| `motion-stopped` | A shade has stopped, with where it stopped. | measured |
| `battery-alert` | Before some moves, with `batteryLevel`, from hard-wired shades too. | measured |
| `shade-online`, `shade-offline` | A shade can be reached again, or no longer. | described |

The description also has `GET /home/scenes/events`, whose events say a scene was added, removed,
activated or deactivated, and only once the shades have stopped; and `GET /home/events`, which
holds the events of both streams. Neither has been measured.

## Where the description can be read

It is not published by Hunter Douglas. The openHAB project keeps a copy as test data of its own
PowerView binding: repository `openhab/openhab-addons`, commit
`02a9c531723a8c775c8e798752a874919fa084d5`, file
`bundles/org.openhab.binding.hdpowerview/src/test/resources/org/openhab/binding/hdpowerview/internal/gen3/openapi.yaml`,
SHA-256 `03e0542c40d461fb567f162b63a3cd4f715388cbc5cde2dbb3404cfb1c3245ac`. It is Hunter
Douglas's text, with no licence to copy it; read it there, and write here only what it says.
