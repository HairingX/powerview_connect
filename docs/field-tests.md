# Field tests

Measurements still to be made on a real home, to settle what `protocol.md` does not yet know.
Each test says what to do, what to record and what is unknown. What a test shows goes into
`protocol.md`, the simulated gateway and the captures, as for every measurement.

The home they were written for has a primary gateway, two auxiliary "Pro" gateways on a cable,
one auxiliary "Std" gateway on Wi-Fi, and 19 hard-wired shades. Everything is done from a phone
on the home's network, with the PowerView app and the Home Assistant app.

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

**Unknown:** whether the gateway sends `shade-offline` and `shade-online`, which the Gen 3 API
describes but no gateway has been seen to send; what a command to an unreachable shade answers.

## 6. The home loses the internet

**Do:** disconnect the router from the internet, not from the home's network. Wait five
minutes. Open `http://<gateway-address>/gateway` in a browser on the phone. Reconnect the router.

**Record:** the page's text, with serial numbers and MAC addresses removed; when the Internet
sensors went off and came back on.

**Unknown:** what `internetState` says without the internet.

## 7. A curtain that stops short of fully open

**Do:** with stall recovery off, open a curtain that is known to stop a little short of fully
open, from the PowerView app. Wait until it has stopped, then one minute more. Then turn stall
recovery on in Home Assistant, and open it again from the app, from its closed position.

**Record:** the log; where the curtain stopped each time, as it looks; whether it was raised the
rest of the way in steps the second time, and whether it then looked fully open.

**Unknown:** whether a curtain that looks stuck has `motion-stopped` `currentPositions` below
the `motion-started` target, as stall recovery expects, or reports the target although it
stopped short; how far short it stops; and whether steps of a third of what is left raise it.
