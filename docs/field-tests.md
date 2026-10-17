# Field tests

Measurements made on a real home, to settle what `protocol.md` does not yet know. Each test says
what to do, what to record and what is unknown, and what it showed once it has been made. What a test shows goes into
`protocol.md`, the simulated gateway and the captures, as for every measurement.

The home they were written for has a primary gateway, two auxiliary "Pro" gateways on a cable,
one auxiliary "Std" gateway on Wi-Fi, and 19 hard-wired shades. Everything is done from a phone
on the home's network, with the PowerView app and the Home Assistant app.

## The home

| Gateway | Model | Primary | Network | Shades |
|---|---|---|---|---|
| Main Room Gateway | Pro | yes | cable | 6 |
| Bedroom Gateway | Pro | no | cable | 4 |
| Hall Gateway | Pro | no | cable | 6 |
| Room 4 Gateway | Std | no | Wi-Fi | 3 |

Every shade is hard-wired. Type 6 is a bottom-up shade (`capabilities` 0); type 8 a
top-down/bottom-up one (`capabilities` 7). A result names a shade by its id, as here, never by
its serial number or Bluetooth name.

## Before each test

1. In Home Assistant, open **Settings → Devices & services → PowerView Connect**, and choose
   **Enable debug logging** in its menu. The log then holds every event the gateway sends, every
   read that fails, and when the gateway stops and starts answering.
2. Note the time, to the second, of each step below as it is done.
3. After the test, choose **Disable debug logging**; Home Assistant hands over the log file.
   Share it with the notes. It holds addresses and names: it is shared, never committed.

## 1. Every shade opens at once, from the app

**Do:** activate the scene that opens every shade in the PowerView app. Wait until every shade
has stopped, then one minute more.

**Record:** the log; whether every shade moved; how long the last one took.

**Unknown:** whether the event stream sends `motion-started` and `motion-stopped` for every one
of 19 shades, and how far apart; whether `motion-stopped` comes late when many move.

**Result, 2026-10-17:** every shade moved, and the stream sent `motion-started` and
`motion-stopped` for each of them, with no error and without reopening. The last shade stopped
82.4 s after the first started. The gateway estimated 36 s for every move.

- The `motion-started` events came within 3.5 s, in groups of one to six, each of shades on one
  gateway: the four of Bedroom Gateway first, within 3 ms.
- The `motion-stopped` events of shades on one gateway that end together came within 5 ms of
  one another, though their moves started up to 1.2 s apart. Whether they came late is not
  known, as the log does not say where a read found a shade; a read's positions are to be
  logged before the next test.
- A `battery-alert` came for 2 of the 19 shades, 154 and 227, before they started.
- Each type 8 shade took 80-81 s; a type 6 shade 18-78 s, by its height.
- With three reads at once, a read of a moving shade took 0.66-3.47 s, half of them within
  1.46 s; each moving shade was read about every 10 s while all moved. `GET /home` took 0.57 s
  just after.

Times are seconds after the first event; "Reads" counts the reads of the shade while it moved.

| Shade | Name | Room | Type | Gateway | Started | Stopped | Took | Reads |
|---|---|---|---|---|---|---|---|---|
| 163 | Bathroom 2 Curtain | Bathroom 2 | 8 | Bedroom Gateway | 0.0 s | 80.9 s | 80.9 s | 11 |
| 231 | Small Hall Curtain | Small Hall | 6 | Bedroom Gateway | 0.0 s | 68.3 s | 68.3 s | 8 |
| 274 | Bedroom Left Curtain | Bedroom | 6 | Bedroom Gateway | 0.0 s | 68.3 s | 68.3 s | 8 |
| 277 | Bedroom Right Curtain | Bedroom | 6 | Bedroom Gateway | 0.0 s | 70.1 s | 70.1 s | 8 |
| 248 | Hall Right Curtain | Hall | 6 | Hall Gateway | 3.4 s | 78.7 s | 75.3 s | 9 |
| 253 | Room 1 Curtain | Room 1 | 6 | Hall Gateway | 3.4 s | 42.5 s | 39.1 s | 4 |
| 256 | Hall Left Curtain | Hall | 6 | Hall Gateway | 3.4 s | 81.2 s | 77.8 s | 9 |
| 259 | Bathroom 1 Curtain | Bathroom 1 | 6 | Hall Gateway | 3.4 s | 70.7 s | 67.3 s | 7 |
| 262 | Room 3 Curtain | Room 3 | 6 | Hall Gateway | 3.4 s | 42.5 s | 39.1 s | 3 |
| 265 | Room 2 Curtain | Room 2 | 6 | Hall Gateway | 3.4 s | 42.5 s | 39.1 s | 3 |
| 150 | Kitchen Right Curtain | Main Room | 8 | Main Room Gateway | 2.5 s | 82.4 s | 79.9 s | 10 |
| 154 | Kitchen Middle Curtain | Main Room | 8 | Main Room Gateway | 1.3 s | 82.4 s | 81.1 s | 10 |
| 218 | Living Room Left Curtain | Main Room | 8 | Main Room Gateway | 2.0 s | 82.1 s | 80.1 s | 10 |
| 223 | Living Room Right Curtain | Main Room | 8 | Main Room Gateway | 2.5 s | 82.4 s | 79.9 s | 10 |
| 227 | Kitchen Left Curtain | Main Room | 8 | Main Room Gateway | 2.0 s | 82.1 s | 80.1 s | 10 |
| 271 | Living Room East Curtain | Main Room | 6 | Main Room Gateway | 2.0 s | 19.7 s | 17.7 s | 1 |
| 268 | Laundry Room Curtain | Laundry Room | 6 | Room 4 Gateway | 3.5 s | 37.1 s | 33.5 s | 3 |
| 280 | Room 4 Curtain | Room 4 | 6 | Room 4 Gateway | 1.0 s | 43.2 s | 42.2 s | 3 |
| 283 | Walk In Curtain | Walk In | 6 | Room 4 Gateway | 1.0 s | 71.2 s | 70.2 s | 8 |

## 2. Every shade closes at once, from Home Assistant

**Do:** select every shade's cover in Home Assistant and close them all in one action, which
sends one command per shade at the same moment. Wait until every shade has stopped, then one
minute more.

**Record:** the log; any error Home Assistant shows; whether every shade moved; whether the
positions in Home Assistant followed the shades while they moved, and how smoothly.

**Unknown:** whether the gateway takes 19 commands at once; how long a read of a moving shade
takes when many move, and so how often each is read.

## 3. An auxiliary gateway loses power

**Do:** pull the power of one auxiliary gateway on a cable. Wait five minutes. Then, for one shade
the gateway serves, move it from Home Assistant, and move it from the app. Put the power back,
and wait until the gateway's Connection sensor is on again, then five minutes more.

**Record:** the log; when the gateway's Connection sensor went off and came back on; whether the
shade moved from each; any error; what the app showed.

**Unknown:** whether the primary gateway reports the shades of a gateway that is gone as
offline (`shade-offline`); whether a command to such a shade fails, and with which error;
whether the primary gateway reaches the shade some other way.

## 4. The primary gateway loses power

**Do:** pull the power of the primary gateway. Wait five minutes. Try to move a shade from Home
Assistant and from the app. Put the power back, and wait until every shade is available in
Home Assistant again, then five minutes more.

**Record:** the log; when the shades went unavailable and came back; whether a shade moved from
the app while the primary gateway was gone.

**Unknown:** whether an auxiliary gateway takes over as primary; what the app does without the
primary gateway.

## 5. A shade loses power

**Do:** pull the power of one shade, where it can be reached. Wait five minutes. Move it from
Home Assistant. Put the power back, and wait five minutes.

**Record:** the log; any error; whether the shade went unavailable in Home Assistant, and when.

**Unknown:** whether the gateway sends `shade-offline` and `shade-online`, which are said to exist
but no gateway has been seen to send; what a command to an unreachable shade answers.

## 6. The home loses the internet

**Do:** disconnect the router from the internet, not from the home's network. Wait five
minutes. Open `http://<gateway-address>/gateway` in a browser on the phone. Reconnect the router.

**Record:** the page's text, with serial numbers and MAC addresses removed; when the Internet
sensors went off and came back on.

**Unknown:** what `internetState` says without the internet.
