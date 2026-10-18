"""What a real Gen 3 gateway (firmware 3.2.59) and the library said on 2026-10-17, while a scene
in the PowerView app opened all 19 hard-wired shades of a home at once, from the captures."""
import json
from pathlib import Path
from typing import cast

from powerview_connect import Rail

CAPTURES = Path(__file__).parents[1] / "captures" / "2026-10-17"


def _load(name: str) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], json.loads((CAPTURES / name).read_text(encoding="utf-8")))


def _positions(raw: object) -> dict[Rail, float]:
    found = cast(dict[str, float], raw or {})
    return {Rail(rail): float(value) for rail, value in found.items() if rail in tuple(Rail)}


def _number(raw: object) -> float:
    return float(cast(float, raw))


SHADES: dict[int, tuple[int, int, int, str]] = {
    int(cast(int, shade["id"])): (int(cast(int, shade["type"])), int(cast(int, shade["capabilities"])),
                                  int(cast(int, shade["batteryStatus"])), str(shade["gateway"]))
    for shade in _load("shades.json")
}
"""Each shade's `type`, `capabilities`, `batteryStatus` and the gateway that serves it."""

EVENTS: list[tuple[float, str, int, dict[Rail, float], dict[Rail, float], float | None]] = [
    (_number(event["seconds"]), str(event["evt"]), int(cast(int, event["id"])),
     _positions(event["currentPositions"]), _positions(event.get("targetPositions")),
     _number(cast(dict[str, object], event["targetPositions"])["etaInSeconds"])
     if "targetPositions" in event else None)
    for event in _load("scene-events.json")
]
"""Seconds after the first event, the kind, the shade, its current and target positions, and the
gateway's estimate of the move's time in seconds."""

READS: list[tuple[float, float, int]] = [
    (_number(read["sent"]), _number(read["took"]), int(cast(int, read["id"])))
    for read in _load("scene-reads.json")
]
"""Seconds after the first event each read of a moving shade was sent, how long it took, and the
shade."""
