"""What a real Gen 3 gateway (firmware 3.2.59) sent on 2026-10-15, with a Duette top-down/bottom-up
shade (shade firmware 3.0.433). Addresses are from the documentation range and serial numbers
are replaced."""

SSE = """100 HELO

data: {"evt":"battery-alert","isoDate":"2026-10-15T13:54:47.917Z","bleName":"DU1:0154","id":154,"currentPositions":{"primary":0,"secondary":0.71,"tilt":0},"batteryLevel":1}

data: {"evt":"motion-started","isoDate":"2026-10-15T13:54:48.474Z","bleName":"DU1:0154","id":154,"currentPositions":{"primary":0,"secondary":0.71,"tilt":0},"targetPositions":{"primary":0,"secondary":0.5,"tilt":0,"etaInSeconds":10}}

data: {"evt":"motion-stopped","isoDate":"2026-10-15T13:55:07.385Z","bleName":"DU1:0154","id":154,"currentPositions":{"primary":0,"secondary":0.5,"tilt":0}}
"""
"""The event stream during one move of the top rail from 0.71 to 0.5."""

POLLS: list[tuple[float, float]] = [
    (0.4, 0.18), (1.7, 0.18), (2.8, 0.18), (3.7, 0.18), (4.9, 0.17), (5.8, 0.16), (7.0, 0.15),
    (8.1, 0.13), (9.0, 0.12), (10.0, 0.11), (11.9, 0.09), (13.7, 0.07), (14.7, 0.04),
    (15.8, 0.03), (17.3, 0.02), (18.3, 0.01), (19.5, 0.0), (20.5, 0.0), (21.4, 0.0), (22.7, 0.0),
    (23.7, 0.0), (24.6, 0.0),
]
"""Seconds after the command and the bottom rail's position, from GET /home/shades/154 polled
about once a second while it moved from 0.18 to 0."""

COMMAND_RESPONSE: dict[str, object] = {
    "err": 0,
    "responses": [{"bleName": "DU1:0154", "err": 0, "id": 154, "hex": "e701"}],
    "wentViaHTTP": True,
}
"""The answer to PUT /home/shades/positions?ids=154."""

HOME: dict[str, object] = {
    "id": "home-0001",
    "rooms": [
        {"_id": 65, "name": "Main Room", "shades": [
            {"id": 154, "type": 8, "ptName": "Kitchen Middle Curtain", "capabilities": 7,
             "powerType": 1, "batteryStatus": 3, "roomId": 65,
             "firmware": {"revision": 3, "subRevision": 0, "build": 433},
             "positions": {"primary": 0, "secondary": 0.71, "tilt": 0, "velocity": 0},
             "bleName": "DU1:0154", "serialNumber": "0000000000000154"},
            {"id": 271, "type": 6, "ptName": "Living Room East Curtain", "capabilities": 0,
             "powerType": 1, "roomId": 65,
             "firmware": {"revision": 3, "subRevision": 0, "build": 391},
             "positions": {"primary": 0, "secondary": 0, "tilt": 1, "velocity": 0},
             "bleName": "DUT:0271"},
        ]},
    ],
    "scenes": [{"_id": 216, "name": "Kitchen Dining", "room_Id": 65,
                "members": [{"shd_Id": 154, "pos": {"pos1": 0, "pos2": 4580}}]}],
    "gateways": [
        {"v": "3.2.59", "serial": "gateway-primary-01", "model": "Pro",
         "name": "Main Room Gateway", "ip": "192.0.2.32", "aux": False},
        {"v": "3.2.59", "serial": "gateway-aux-02", "model": "Pro",
         "name": "Bedroom Gateway", "ip": "192.0.2.31", "aux": True},
    ],
}
"""/home, trimmed to two shades and one scene; its structure is verbatim."""
