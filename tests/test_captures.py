"""The captures of what real gateways sent: each is read as the library reads it, and identifies
no one."""
import json
import re
from pathlib import Path
from typing import cast

import pytest

from powerview_connect import Rail
from powerview_connect._model import command_errors, parse_event, parse_gateway, parse_home, parse_shade
from powerview_connect._motion import ShadeTracker

CAPTURES = Path(__file__).parents[1] / "captures"
PLACEHOLDERS = {
    "serial": r"gateway-\d+",
    "serialNumber": r"\d{16}|gateway-\d+",
    "bleName": r"[A-Z0-9]{3}:\d{4}",
    "homeId": r"home-0001",
    "key": r"0+",
}
"""What the captures put in place of the values that identify a person's devices or home."""


def _json(path: str) -> dict[str, object]:
    return cast(dict[str, object], json.loads((CAPTURES / path).read_text(encoding="utf-8")))


def _files() -> list[Path]:
    return sorted(path for path in CAPTURES.rglob("*") if path.suffix in (".json", ".sse"))


def test_the_home_is_read_with_every_shade_gateway_and_scene() -> None:
    home = parse_home(_json("2026-10-16/home.json"))
    assert (len(home.shades), len(home.gateways), len(home.scenes)) == (19, 4, 39)
    assert {shade.type for shade in home.shades.values()} == {6, 8}


@pytest.mark.parametrize(("path", "interface"), [
    ("2026-10-16/gateway-auxiliary-pro.json", "eth0"),
    ("2026-10-16/gateway-auxiliary-std-wifi.json", "wlan0"),
])
def test_a_gateways_own_state_is_read(path: str, interface: str) -> None:
    status = parse_gateway(_json(path))
    assert (status.is_primary, status.interface) == (False, interface)


def test_a_shade_events_and_a_command_answer_are_read() -> None:
    assert parse_shade(_json("2026-10-15/shade.json")).id == 154
    lines = (CAPTURES / "2026-10-15/events.sse").read_text(encoding="utf-8").splitlines()
    assert [event.kind for line in lines if (event := parse_event(line))] == [
        "battery-alert", "motion-started", "motion-stopped"]
    assert command_errors(_json("2026-10-15/positions-answer.json")) == []


def test_every_event_of_the_scene_names_a_shade_of_the_home() -> None:
    events = cast(list[dict[str, object]],
                  json.loads((CAPTURES / "2026-10-17/scene-events.json").read_text(encoding="utf-8")))
    home = parse_home(_json("2026-10-16/home.json"))
    assert {event["id"] for event in events} <= set(home.shades)
    stopped = [event for event in events if event["evt"] == "motion-stopped"]
    assert len(stopped) == 19
    assert all(cast(dict[str, float], event["currentPositions"])[Rail.PRIMARY] == 1
               for event in stopped)


def _private(value: object, found: list[tuple[str, str]]) -> list[tuple[str, str]]:
    if isinstance(value, dict):
        for key, item in cast(dict[str, object], value).items():
            if key in PLACEHOLDERS and isinstance(item, str):
                found.append((key, item))
            _private(item, found)
    elif isinstance(value, list):
        for item in cast(list[object], value):
            _private(item, found)
    return found


@pytest.mark.parametrize("path", _files(), ids=lambda path: str(path.relative_to(CAPTURES)))
def test_a_capture_holds_placeholders_where_a_real_one_identifies_someone(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    documents: list[object] = (
        [json.loads(line.removeprefix("data:")) for line in text.splitlines()
         if line.startswith("data:")] if path.suffix == ".sse" else [json.loads(text)])
    for document in documents:
        for key, value in _private(document, []):
            assert re.fullmatch(PLACEHOLDERS[key], value), (key, value)
        if isinstance(document, dict):
            home = cast(dict[str, object], document).get("home")
            if isinstance(home, dict):
                assert cast(dict[str, object], home).get("loc") == {"latitude": 0.0, "longitude": 0.0}


def test_every_capture_is_described() -> None:
    readme = (CAPTURES / "README.md").read_text(encoding="utf-8")
    for path in _files():
        assert f"`{path.relative_to(CAPTURES).as_posix()}`" in readme, path


def _replay_stalls() -> list[tuple[float, int, float]]:
    """Each motion-stopped of the stalls of 2026-10-18 that ends no move, with the commands sent
    taken as the tracker takes them: when, which shade, and where it ended."""
    events = cast(list[dict[str, object]], json.loads(
        (CAPTURES / "2026-10-18/stall-events.json").read_text(encoding="utf-8")))
    moves = cast(list[dict[str, object]], json.loads(
        (CAPTURES / "2026-10-18/stall-moves.json").read_text(encoding="utf-8")))
    timeline = sorted(
        [(cast(float, event["seconds"]), 1, event) for event in events]
        + [(cast(float, move["seconds"]), 0, move) for move in moves],
        key=lambda item: (item[0], item[1]))
    trackers: dict[int, ShadeTracker] = {}
    found: list[tuple[float, int, float]] = []
    for seconds, is_event, item in timeline:
        shade_id = cast(int, item["id"])
        tracker = trackers.setdefault(shade_id, ShadeTracker())
        if not is_event:
            tracker.command(seconds, {Rail.PRIMARY: cast(float, item["primary"])})
            continue
        event = parse_event("data: " + json.dumps(item))
        assert event is not None
        if event.kind == "motion-started":
            tracker.motion_started(event, seconds)
        elif event.kind == "shade-online":
            tracker.snapshot(event.current)
        elif event.kind == "motion-stopped":
            before = dict(tracker.last_target)
            tracker.motion_stopped(event)
            if before and not tracker.last_target:
                found.append((seconds, shade_id, event.current[Rail.PRIMARY]))
    return found


def test_the_curtains_that_reached_the_top_and_slid_down_end_no_move() -> None:
    found = _replay_stalls()
    assert [(shade_id, at) for _, shade_id, at in found] == [(262, 0.95), (280, 0.95)]
