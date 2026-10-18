# The PowerView Gen 3 gateway, as measured

Everything here was observed on a real gateway (firmware 3.2.59, 2026-10-15 and 16) with a Duette
top-down/bottom-up shade (shade firmware 3.0.433), on 2026-10-17 with the whole home it serves -
19 hard-wired shades of two types, on four gateways. What Hunter Douglas's description of the API
says beside it, in this project's own words: `api.md`. The library is built on it; the simulated gateway behaves
as it says.

## Endpoints

| Request | Answer |
|---|---|
| `GET /home` | the whole home: `gateways` (`_id`, `serial`, `name`, `ip`, `mac`, `ssid`, `model`, `v`, `radio1V`, `radio2V`, `aux`, `shd_Ids`), `rooms` with their `shades`, `scenes`, `remotes` |
| `GET /gateway` | the gateway itself, as `config`: `serialNumber`, `model`, `brand`, `hwVersion`, `firmware` (`mainProcessor` and `radios`, each `revision`, `subRevision`, `build`), `networkStatus` (`ipAddress`, `activeInterface`, `internetState`, `ssid`, and `eth0`/`wlan0`), `mgwConfig.primary`, `mgwStatus.running`; auxiliary gateways answer it too |
| `GET /home/shades/{id}` | one shade; while it moves its `positions` change live |
| `PUT /home/shades/positions?ids=1,2` with `{"positions": {...}}` | `{"err": 0, "responses": [{"id", "bleName", "err", "hex"}], "wentViaHTTP": true}` |
| `PUT /home/shades/stop?ids=1,2` | as for positions |
| `PUT /home/scenes/{id}/activate` | not measured |
| `PUT /home/shades/{id}/motion` with `{"motion": "jog"}` | the shade jogs to show which one it is; any other motion, such as `"heart"` for the favorite position, is refused with HTTP 400 `{"errMsg": "motion property must be one of [\"jog\"]"}` (measured) |
| `GET /home/shades/events?sse=true` | the event stream |

A shade has `id`, `name` (base64 of `ptName`), `ptName`, `type`, `capabilities`, `powerType`
(1 is hard-wired), `batteryStatus`, `roomId`, `firmware` (`revision`, `subRevision`, `build`),
`motion`, `positions`, `signalStrength`, `bleName`, `shadeGroupIds` and, on some,
`serialNumber`. `signalStrength` is the gateway's reception of the shade in dBm (-66 measured).
A hard-wired shade reports `batteryStatus` 3 or, for type 6, 1, so the number alone does not say
a shade has a battery: `powerType` does. The API description gives `batteryStatus` only as a
rough gauge from 0 to 3; what a battery shade reports has not been measured. Positions run from 0.0, fully closed, to 1.0, fully open, per rail. On a
top-down/bottom-up shade, `primary` is the bottom rail and `secondary` the top rail.

The primary gateway's `/home` lists every gateway, auxiliary ones (`aux: true`) too, and every
shade. `shd_Ids` lists the shades a gateway serves; each shade was listed under one gateway.
A gateway on Wi-Fi lists its `ssid`; one on a cable lists `""`. `radio2V` was `""` on a
"Std" gateway, which may have one radio. `remotes` was empty in a home without remotes; what
it lists for a remote has not been measured.

`GET /gateway` was answered alike by the primary and an auxiliary gateway, but for
`mgwConfig.primary`. `activeInterface` was `eth0` on a cable, with `eth0.type` `Wired` and an
empty `wlan0`; it was `wlan0` on Wi-Fi, with `wlan0.type` `Wireless`, the network in `ssid` and
an empty `eth0`. A "Std" gateway listed one radio, a "Pro" two. `internetState` was `Connected`; what it
says without internet has not been measured. The primary gateway's event stream reports the
shades of auxiliary gateways too. Tilting, dual-overlapped and battery shades have not been
followed live yet.

## Shade types

| `type` | `capabilities` | Bluetooth name starts | Firmware | `batteryStatus` | `positions` | Fully open from closed |
|---|---|---|---|---|---|---|
| 6 | 0, bottom up | `DUT:` | 3.0.391 | 1, though hard-wired | `tilt` 1.0, though it has no vanes | 18-78 s, by the shade's height |
| 8 | 7, top-down/bottom-up | `DU1:` | 3.0.433 | 3 | `tilt` 0.0 | 80-81 s |


## What was measured, and what follows from it

| Observation | Consequence |
|---|---|
| `{"positions": {"primary": 0.2}}` then `{"positions": {"secondary": 0.5}}`, back to back: **both rails move**. The answer's hex says "unchanged" (`0x8000`) for the rail not sent; the API description says such a rail is not changed. | A command sends only its own rail, never both from a cache. |
| A crossing move (primary 0.5, then secondary 0.6): the **shade's firmware stops** the top rail at 0.5, where it meets the bottom rail. | No clamp is needed in software. |
| The event stream opens with `100 HELO`, then sends `data: {json}` lines: `motion-started` (with `targetPositions` and `etaInSeconds`, for every source: this client, the app, a voice assistant), `motion-stopped` (final `currentPositions`), `battery-alert` (`batteryLevel: 1`, for hard-wired shades too: before every move of one shade, but
before the moves of only 2 of 19 shades that a scene moved at once). The API description also has `shade-offline`, not yet seen. No positions are sent during a move that began with a `motion-started`. | The stream says when a move starts and ends; nothing is polled while nothing moves. |
| `motion-started` arrives right after the command; the motor starts 4-5 s later. | "No change yet" is not "stopped". |
| `etaInSeconds` against the real time from started to stopped: 5 → 9.6 s, 6 → 10.7, 9 → 16.6, 10 → 19.0-19.2, 19 → 41.8. A scene opening 19 shades gave 36 for every one; they took 17.7-81.1 s. | The estimate does not decide when a move has ended. |
| A scene in the app opening 19 shades: every `motion-started` came within 3.5 s, and every `motion-stopped`. Both come in groups by the gateway that serves the shades: the first group within 3 ms, the next ones 1-3.5 s later, and stops of shades on one gateway within 5 ms of one another, though their moves started up to 1.2 s apart. | Every shade of a scene is followed on its own; whether a grouped stop comes late is not known. |
| `GET /home/shades/{id}` with 19 shades moving and three reads at once: 0.66-3.47 s, half of them within 1.46 s. `GET /home` right after: 0.57 s. | With 19 moving, each shade is read about every 10 s. |
| `GET /home/shades/{id}` gives **live positions** while the shade moves: 0.01 steps, about once a second, ramping up and down. | A moving shade is polled about once a second, and only while it moves. |
| `motion-stopped` normally comes on time; once, for the largest two-rail move, perhaps 14-23 s late (not proven). | A move also ends when the target is reached and still for 1.5 s, when nothing changes for 6 s, or at a timeout. |
| Both rails in one request: they start about 1 s apart and run together. | Moving both rails is one request. |
| `shade-online` with `currentPositions` comes about every 2 s while a shade standing still moves by itself (below), and once, within 2 s, for every shade of the three auxiliary gateways, with where each stood. | It says where a shade standing still is. |
| Two Duette shades (type 6) that reached the top were **backed off by their motor** to 0.95 within 4-6 s: `shade-online` on the way, then a `motion-stopped`, with no `motion-started`. Small moves up from there, of 0.01-0.02, stayed where they stopped; the next move to 1.0 backed off again. | A motor backing off after a move sent fully open counts as much as one on the way up, and the top is reached in small steps. |
| An opening of such a shade **stalled**: from 0.9 it stopped at 0.91 after 2 s; from 0.95 and from 0.86, `motion-started` and `motion-stopped` came 1-4 ms apart with no movement. | A stall ends with the move's own `motion-stopped`, short of the target. |
| After a stall the motor **backed off downwards** by 0.05 in 5-7 s, with `shade-online` on the way and a `motion-stopped`, and **no `motion-started`**. Each time a command was sent within 15 ms of the stall, whatever its target (0.94, 0.91, 0.97 above where the shade stood), and it brought nothing else; once the first `shade-online` of the backing off came 4 ms before that command, so the motor may back off by itself. | A retry waits for the `motion-stopped` alone, and lowers the shade itself, as a stall may not always be followed by a backing off. |
| An opening sent 3 s and 6 s after such a backing off had stopped opened the shade fully (0.81 to 1.0 in 10 s, 0.9 to 1.0 in 6 s); one sent 158 s after it stalled again. An opening sent at once was not measured. | A retry raises the shade once it has sent no event for 3 s after lowering it. |
