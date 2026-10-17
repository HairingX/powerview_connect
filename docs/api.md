# The Gen 3 gateway's API, as far as it is known

Everything known of the gateway's local HTTP API, written in this project's own words, so a
change can be planned without another source. Each statement says how it is known:

- **measured**: seen on a real gateway; `captures/` holds what it sent.
- **described**: stated by Hunter Douglas's description of the API, version 1.10.0 (OpenAPI
  3.0.3), and not measured. The description is not kept here; where to read it is at the end.

Where the two disagree, the measurement is right. Nothing marked "described" has been sent to a
gateway by this project.

## Conventions

- **Address.** Plain HTTP on port 80 of the gateway, no authentication (measured).
- **Documents.** JSON in both directions (measured).
- **Names.** A shade's name is given twice: `ptName` as text, and `name` as the same text
  Base64-encoded (measured). In `GET /home`, a room's and a scene's `name` is plain text
  (measured); the description gives rooms and scenes of their own requests both forms.
- **Shades in a command.** A command names its shades in the query string, by id with
  `?ids=12,42` or by Bluetooth name with `?shades=PIR:1234,SON:9AE2`, never both. The description
  prefers ids, as an id stays the same when a motor is replaced (described; ids measured).
- **Positions.** Every rail runs from 0.0, fully closed, to 1.0, fully open (measured).
- **Errors.**
  - An error is `{"errMsg": "..."}` (measured, for HTTP 400).
  - HTTP 503 means the gateway has no home yet; HTTP 404 means an id it does not know (described).
- **A command's answer** is `{"err": 0, "responses": [...]}`, one entry per shade:
  - `id` and `bleName`;
  - `hex`, the shade's own encoded answer, where it took the command (measured);
  - `err` where it did not (described).

## The home

| Request | Gives | Known |
|---|---|---|
| `GET /home` | The whole home in one document; the description marks it as for Hunter Douglas's own apps. | measured |
| `GET /home/shades` | Every shade, as a list of shade documents. | described |
| `GET /home/shades/{id}` | One shade. While it moves, its `positions` change about once a second. | measured |
| `GET /home/rooms` | Every room; with `?shades=true` each room holds its shades. | described |
| `GET /home/rooms/{id}` | One room. | described |
| `GET /home/scenes` | Every scene. | described |
| `GET /home/scenes/active` | The scenes now in effect. | described |
| `GET /home/scenes/{id}` | One scene. | described |
| `GET /home/automations`, `GET /home/automations/{id}` | The schedules that activate scenes. | described |
| `GET /home/id` | The home's identifier in Hunter Douglas's cloud. | described |
| `PUT`, `DELETE /home/integration/{name}` | Adds a name to, or removes it from, the home's list of integrations that use it. | described |

### The whole home, `GET /home`

Measured top-level keys:
- `id`, `_schemaVersion`, `createdBy`, `createdDate`, `modApp`, `modDate`, `hkHome`, `gHome`, `aHome`;
- `home` (country, time zone, location, a key);
- `rooms` (each with its `shades`), `scenes`, `gateways`, `remotes`, `automations`, `controllers`.

Each gateway in it has:
- `_id`, `name`, `serial`, `model` ("Pro" or "Std"), `aux`;
- `ip`, `mac`, `ssid` (empty on a cable);
- `v` (firmware) and `radio1V`/`radio2V` (radio firmware);
- `shd_Ids`, the shades it serves.

Each room has `_id`, `name`, `color`, `icon`, `type`, `groups` and its `shades`. Each scene has
`_id`, `name`, `color`, `icon`, `bleId`, `modDate`, `members` and, on most, `room_Id`. Each
member has its own `_id`, the shade's `shd_Id`, and `pos`, the shade's positions `pos1` and
`pos2` from 0 to 10000 (measured).

### A shade

| Field | Meaning | Known |
|---|---|---|
| `id` | The shade's number. | measured |
| `type` | The kind of product; a number for Hunter Douglas's own use. | measured |
| `name`, `ptName` | The name. | measured |
| `capabilities` | How the shade moves; see below. | measured for 0 and 7, described for the others |
| `powerType` | 0 battery, 1 hard-wired, 2 rechargeable. | measured for 1, described for the others |
| `batteryStatus` | A rough gauge of the battery, 0 to 3, each number without a stated meaning. Hard-wired shades report 3, or 1 for type 6. | measured for hard-wired shades |
| `roomId` | The room it is in. | measured |
| `signalStrength` | The gateway's average reception of it, RSSI in dBm. | measured |
| `positions` | `primary`, `secondary`, `tilt`, and `velocity`, the speed from 0.0 to 1.0. | measured |
| `firmware` | `revision`, `subRevision`, `build`. | measured |
| `bleName` | Its Bluetooth name; the first three letters say the kind, such as `DUT` or `DU1`. | measured |
| `serialNumber`, `motion`, `shadeGroupIds` | Present on some shades; the description lists none of them. | measured |

| `capabilities` | Moves | Rails | Known |
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

Where a front and a rear shade share one motor, the shade's own firmware puts the front one where
it has to be when the rear one moves, so a controller need not (described). Where a top and a
bottom rail would cross, the shade's firmware stops the moving one where they meet (measured).

### Rooms, scenes and automations

| Document | Fields | Known |
|---|---|---|
| Room | `id`, `name`, `ptName`, `color` and `icon` (numbers given as text), `type`, and `shades` when asked for | described |
| Scene | `id`, `name`, `ptName`, `color`, `icon`, `networkNumber`, `roomIds` | described |
| Automation | `id`, `type`, `enabled`, `days`, `hour`, `min`, `bleId`, `sceneId`, `errorShd_Ids` | described |

An automation's `type` says what its time counts from:
- 0, the clock;
- 2, before sunrise;
- 6, before sunset;
- 10, after sunrise;
- 14, after sunset.

`hour` and `min` are the time of day for the clock, and an offset otherwise. `days` holds one bit
per day, from Monday, 0x01, to Sunday, 0x40. `bleId` is the automation's number in the shades
themselves. `errorShd_Ids` lists the shades that could not be given it (described).

## Commands

| Request | Does | Known |
|---|---|---|
| `PUT /home/shades/positions?ids=...` with `{"positions": {...}}` | Moves the shades. Only the rails given move. `velocity`, from 0.0 to 1.0, sets the speed; left out, the default. | measured; the description says the same of a rail left out |
| `PUT /home/shades/{id}/positions` with `{"positions": {...}}` | Moves one shade, and answers with the `motion-started` event, target and estimate included. | described |
| `PUT /home/shades/stop?ids=...` | Stops the shades, every rail. | measured |
| `PUT /home/shades/{id}/motion` with `{"motion": "jog"}` | The shade jogs, to show which one it is. Any other motion is refused with HTTP 400. | measured; the description lists only `jog` |
| `PUT /home/scenes/{id}/activate` | Moves the scene's shades; answers with their ids. | described |
| `GET`, `PUT /home/shades/time?ids=...` | Reads each shade's clock, or sets it to now. | described |

The positions document also takes `servo3`, a third rail, which no measured shade has (described).

## The event streams

All three are server-sent event streams: `100 HELO`, then a `data: {json}` line for each event
(measured for the shades' stream).

`GET /home/shades/events?sse=true` sends one event per change of a shade (measured):
- `evt`, `isoDate`, `bleName` and `id`;
- `currentPositions`, where the shade is.

| `evt` | When | Known |
|---|---|---|
| `motion-started` | A shade starts to move, whatever started it. It carries `targetPositions`, with `etaInSeconds`, the gateway's estimate of how long the move takes; the description says the target may be left out when it is not known. | measured |
| `motion-stopped` | A shade has stopped, and where. | measured |
| `battery-alert` | Before some moves, with `batteryLevel`, hard-wired shades too. | measured |
| `shade-online`, `shade-offline` | A shade can be reached again, or no longer. | described |

`GET /home/scenes/events` sends `scene-add`, `scene-del`, `scene-activated` and
`scene-deactivated`, with the scene's document. An activation is only sent once its shades
have stopped (described).

`GET /home/events` sends the events of both streams (described).

`GET /home/events/listeners` lists the gateway's places for streams and those in use, and
`DELETE` on it closes every stream; both are for Hunter Douglas's own use (described). They
suggest the gateway takes only so many streams at once.

## The gateway

Every gateway answers these, auxiliary ones too, though the home's requests above go to the
primary gateway (measured for `GET /gateway`).

| Request | Gives or does | Known |
|---|---|---|
| `GET /gateway` | Its configuration and state. | measured |
| `GET /gateway/info` | Its serial number and firmware version. | described |
| `GET /gateway/info/gateways` | What it knows of every gateway of the home. | described |
| `GET /gateway/network`, `/gateway/network/status`, `/gateway/network/config` | Its network: interface, addresses, internet state. | described; the same fields in `GET /gateway` measured |
| `GET /gateway/internet/status` | Checks its connection to the internet now: `success`, `type`, `message`. | described |
| `GET /gateway/radios` | Its radio's serial number and firmware versions. | described |
| `GET /gateway/shades/discover` | The shades it hears over Bluetooth, each with a filtered RSSI; `?rssiMinValue=` leaves out the weaker. | described |
| `GET`, `PUT /gateway/identify` | Flashes its light, to show which one it is; `?time=` in seconds. | described |
| `GET`, `PUT /gateway/led` | Its light's colour and brightness: `red`, `green`, `blue`, `brightness`. | described; the values in `GET /gateway` measured |
| `GET /gateway/log`, `GET`, `PUT /gateway/log/level` | Its log, and how much it logs. | described |
| `GET /gateway/update/channel` | Its firmware channel: `coredev`, `nightly`, `qa`, `beta` or `production`. | described |
| `GET /gateway/update/artifact-info` | A description of its firmware build. | described |
| `GET /gateway/homekit` | Its HomeKit bridge's state. | described |

### `GET /gateway`

Measured: `config` with:
- `serialNumber`, `model`, `brand` ("Luxaflex" here), `hwVersion`;
- `firmware`: `mainProcessor` and a list of `radios`, each `revision`, `subRevision`, `build`;
- `networkStatus`:
  - `ipAddress`, `activeInterface` (`eth0` on a cable, `wlan0` on Wi-Fi), `primaryMacAddress`;
  - `eth0` and `wlan0` (each `type` `Wired` or `Wireless`, address, netmask, router);
  - `internetState` (`Connected`; the description adds `Disconnected`), `ssid`;
- `networkConfig` (static address or not);
- `mgwConfig.primary` and `mgwStatus.running`;
- `rangeMapper`, `color`, `homeAssoc`, `cloudConfig`, `networkVPN`.

Beside `config`, `manifest` gives the firmware's version and its components' versions.

### `GET /gateway/info/gateways`

`known` maps each gateway the primary one knows to its state (described):
- `st`: 0 starting, 1 idle, 2 a new gateway without a network, 3 no internet, 4 no home;
- `pri`: present when it is the primary gateway;
- `self`: present when it is the gateway asked.

`missing` lists the ids of the home's gateways that have not reported lately.

## Not for this library

The description has more, which a controller of shades has no use for and must not send, as it
changes or resets the gateway:
- the factory reset, the reboot and the firmware update;
- the Wi-Fi and network settings, and the cloud settings;
- HomeKit's pairing and resets;
- permission for SSH;
- raw commands to shades (`/home/shades/exec`);
- the shades' factory data and their time syncing;
- clearing a radio;
- everything under `/sps/`, which is for another Hunter Douglas device.

Most are marked for Hunter Douglas's own use.

## Where the description can be read

Hunter Douglas has not published it. The openHAB project keeps a copy as test data of its own
PowerView binding:
- repository: `openhab/openhab-addons`;
- commit: `02a9c531723a8c775c8e798752a874919fa084d5`;
- file: `bundles/org.openhab.binding.hdpowerview/src/test/resources/org/openhab/binding/hdpowerview/internal/gen3/openapi.yaml`;
- SHA-256: `03e0542c40d461fb567f162b63a3cd4f715388cbc5cde2dbb3404cfb1c3245ac`.

It is Hunter Douglas's text, with no licence to copy it. Read it there, and write here only what
it says, in this project's own words.
