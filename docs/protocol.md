# The PowerView Gen 3 gateway, as measured

Everything here was observed on a real gateway (firmware 3.2.59, 2026-10-15) with a Duette
top-down/bottom-up shade (shade firmware 3.0.433), or is stated by Hunter Douglas's Gen 3
OpenAPI description where it says so. The library is built on it; the simulated gateway behaves
as it says.

## Endpoints

| Request | Answer |
|---|---|
| `GET /home` | the whole home: `gateways` (`serial`, `name`, `ip`, `model`, `v`, `aux`), `rooms` with their `shades`, `scenes` |
| `GET /home/shades/{id}` | one shade; while it moves its `positions` change live |
| `PUT /home/shades/positions?ids=1,2` with `{"positions": {...}}` | `{"err": 0, "responses": [{"id", "bleName", "err", "hex"}], "wentViaHTTP": true}` |
| `PUT /home/shades/stop?ids=1,2` | as for positions |
| `PUT /home/scenes/{id}/activate` | not measured |
| `GET /home/shades/events?sse=true` | the event stream |

A shade has `id`, `name` (base64 of `ptName`), `ptName`, `type`, `capabilities`, `powerType`
(1 is hard-wired), `batteryStatus`, `roomId`, `firmware` (`revision`, `subRevision`, `build`),
`motion`, `positions`, `signalStrength`, `bleName`, `shadeGroupIds` and, on some,
`serialNumber`. `signalStrength` is the gateway's reception of the shade in dBm (-66 measured).
A hard-wired shade reports `batteryStatus` 3, so the number alone does not say a shade has a
battery; what it means for a battery shade has not been measured. Positions run from 0.0, fully closed, to 1.0, fully open, per rail. On a
top-down/bottom-up shade, `primary` is the bottom rail and `secondary` the top rail.

The primary gateway's `/home` lists every gateway, auxiliary ones (`aux: true`) too, and every
shade. Shades on auxiliary gateways have not been followed live yet; nor tilting,
dual-overlapped or battery shades.

## What was measured, and what follows from it

| Observation | Consequence |
|---|---|
| `{"positions": {"primary": 0.2}}` then `{"positions": {"secondary": 0.5}}`, back to back: **both rails move**. The answer's hex says "unchanged" (`0x8000`) for the rail not sent; the API description says such a rail "is not changed". | A command sends only its own rail, never both from a cache. |
| A crossing move (primary 0.5, then secondary 0.6): the **shade's firmware stops** the top rail at 0.5, where it meets the bottom rail. | No clamp is needed in software. |
| The event stream opens with `100 HELO`, then sends `data: {json}` lines: `motion-started` (with `targetPositions` and `etaInSeconds`, for every source: this client, the app, a voice assistant), `motion-stopped` (final `currentPositions`), `battery-alert` (`batteryLevel: 1`, right before every move, for hard-wired shades too). The API description also lists `shade-online` and `shade-offline`. No positions are sent during a move. | The stream says when a move starts and ends; nothing is polled while nothing moves. |
| `motion-started` arrives right after the command; the motor starts 4-5 s later. | "No change yet" is not "stopped". |
| `etaInSeconds` against the real time from started to stopped: 5 → 9.6 s, 6 → 10.7, 9 → 16.6, 10 → 19.0-19.2, 19 → 41.8. | The estimate does not decide when a move has ended. |
| `GET /home/shades/{id}` (without `?refresh=true`) gives **live positions** while the shade moves: 0.01 steps, about once a second, ramping up and down. | A moving shade is polled about once a second, and only while it moves. |
| `motion-stopped` normally comes on time; once, for the largest two-rail move, perhaps 14-23 s late (not proven). | A move also ends when the target is reached and still for 1.5 s, when nothing changes for 6 s, or at a timeout. |
| Both rails in one request: they start about 1 s apart and run together. | Moving both rails is one request. |
