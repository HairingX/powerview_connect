# Captures

What real PowerView Gen 3 gateways sent, kept so the format of every answer and event is known
while the library is developed, and so a change in a later firmware can be told from what came
before. Each folder is the day it was captured. `docs/protocol.md` says what the captures
show; the tests read every capture as the library reads it.

## What is hidden

A capture keeps every key and the type and shape of every value. Values that identify a
person's devices or home are replaced, the same way in every file:

| Value | Replaced by |
|---|---|
| a gateway's `serial`, `serialNumber` | `gateway-<its _id>` |
| a shade's `serialNumber` | its id, as 16 digits |
| `bleName` | its prefix, which says the shade's kind, and the shade's id: `DU1:0154` |
| IP addresses | addresses of the documentation range 192.0.2.0/24, one per real one |
| MAC addresses | addresses of the documentation range 00:00:5E:00:53:00/24 |
| the home's `id` and `cloudConfig.homeId` | `home-0001` |
| the home's `key` | zeros |
| the home's `loc` | latitude and longitude 0.0 |
| `homeAssoc` | 1 |

Names of rooms, shades, scenes and gateways are kept: they say what each shade is.

## 2026-10-15: one top-down/bottom-up shade

Primary gateway firmware 3.2.59, a Duette top-down/bottom-up shade with firmware 3.0.433.

| File | What it is |
|---|---|
| `2026-10-15/events.sse` | `GET /home/shades/events?sse=true` during one move of the top rail from 0.71 to 0.5, as sent |
| `2026-10-15/shade.json` | `GET /home/shades/154` |
| `2026-10-15/positions-answer.json` | the answer to `PUT /home/shades/positions?ids=154` |
| `2026-10-15/moving-shade-reads.json` | seconds after the command, and the bottom rail's position, from `GET /home/shades/154` read about once a second while it moved from 0.18 to 0 |

## 2026-10-16: the whole home

Four gateways with firmware 3.2.59 - the primary and two auxiliary "Pro" ones on a cable, an
auxiliary "Std" one on Wi-Fi - and 19 hard-wired shades.

| File | What it is |
|---|---|
| `2026-10-16/home.json` | `GET /home` from the primary gateway, whole |
| `2026-10-16/gateway-auxiliary-pro.json` | `GET /gateway` of an auxiliary "Pro" gateway on a cable |
| `2026-10-16/gateway-auxiliary-std-wifi.json` | `GET /gateway` of an auxiliary "Std" gateway on Wi-Fi |
| `2026-10-16/motion-heart-refused.json` | the HTTP 400 answer to `PUT /home/shades/{id}/motion` with `{"motion": "heart"}` |

## 2026-10-17: a scene opening every shade

A scene in the PowerView app opened all 19 shades of the home of 2026-10-16 at once. These are
taken from the library's debug log, so they hold what the library parsed, not the lines sent:
an event has no `isoDate` or `bleName`, and `seconds` is the time after the first event.

| File | What it is |
|---|---|
| `2026-10-17/shades.json` | each shade's `type`, `capabilities`, `batteryStatus` and the gateway that serves it |
| `2026-10-17/scene-events.json` | every event of the event stream, in the order it came |
| `2026-10-17/scene-reads.json` | every `GET /home/shades/{id}` of a moving shade: when it was sent, how long it took, and the shade; three at most at once |

## 2026-10-18: pleated shades that stall at the top

Two hard-wired Duette shades of type 6 of the home of 2026-10-16, each served by an auxiliary
gateway, with the retry of a stalled opening of the library's 1.3.0 turned on: it raised them in
small steps. Taken from the library's debug log, as on 2026-10-17; `seconds` is the time after
the first line. The log began while the first retry ran.

| File | What it is |
|---|---|
| `2026-10-18/stall-events.json` | every event of the event stream, in the order it came |
| `2026-10-18/stall-moves.json` | every move Home Assistant sent: when its answer came, the shade, the primary position sent, how long the answer took, and whether the retry or the user in Home Assistant sent it; moves from the PowerView app show only as events |

## Adding a capture

Put it in a folder of the day it was captured, hide what the table above says, describe it
here, and let the tests read it.
