"""Parsing what the gateway sends, from captures of a real one."""
import pytest

from captured import COMMAND_RESPONSE, HOME, SSE
from powerview_connect import (
    Capabilities,
    EventKind,
    Rail,
    command_errors,
    parse_event,
    parse_home,
    parse_positions,
    parse_shade,
)


def test_the_event_stream_yields_its_events_and_skips_the_greeting() -> None:
    events = [event for line in SSE.splitlines() if (event := parse_event(line)) is not None]
    assert [event.kind for event in events] == [
        EventKind.BATTERY_ALERT, EventKind.MOTION_STARTED, EventKind.MOTION_STOPPED]
    started = events[1]
    assert started.shade_id == 154
    assert started.current == {Rail.PRIMARY: 0.0, Rail.SECONDARY: 0.71, Rail.TILT: 0.0}
    assert started.target == {Rail.PRIMARY: 0.0, Rail.SECONDARY: 0.5, Rail.TILT: 0.0}
    assert started.eta == 10
    assert events[2].target == {} and events[2].eta is None


@pytest.mark.parametrize("line", [
    "100 HELO", "", "data: not json", "data: [1, 2]", 'data: {"evt": "motion-started"}',
    'data: {"id": 154}', 'data: {"evt": "motion-started", "id": "154"}',
])
def test_a_line_that_is_no_shade_event_is_none(line: str) -> None:
    assert parse_event(line) is None


def test_an_event_of_a_kind_not_known_is_kept() -> None:
    event = parse_event('data: {"evt": "something-new", "id": 3}')
    assert event is not None and event.kind == "something-new" and event.current == {}


def test_the_home_names_its_gateways_shades_and_scenes() -> None:
    home = parse_home(HOME)
    assert home.home_id == "home-0001"
    assert home.primary_gateway is not None
    assert home.primary_gateway.serial == "gateway-primary-01"
    assert home.primary_gateway.firmware == "3.2.59"
    assert [gateway.is_primary for gateway in home.gateways] == [True, False]
    shade = home.shades[154]
    assert (shade.name, shade.capabilities, shade.room_name, shade.firmware, shade.power_type) == (
        "Kitchen Middle Curtain", 7, "Main Room", "3.0.433", 1)
    assert shade.capability is Capabilities.TOP_DOWN_BOTTOM_UP
    assert shade.positions == {Rail.PRIMARY: 0.0, Rail.SECONDARY: 0.71, Rail.TILT: 0.0}
    assert (shade.ble_name, shade.serial) == ("DU1:0154", "0000000000000154")
    assert home.shades[271].serial is None
    assert [(scene.id, scene.name, scene.room_id) for scene in home.scenes] == [
        (216, "Kitchen Dining", 65)]


def test_a_gateway_is_found_by_its_address_with_or_without_a_port() -> None:
    home = parse_home(HOME)
    primary, auxiliary = home.gateway_at("192.0.2.32"), home.gateway_at("192.0.2.31:80")
    assert primary is not None and primary.is_primary
    assert auxiliary is not None and not auxiliary.is_primary
    assert home.gateway_at("192.0.2.99") is None
    assert home.gateway_at("2001:db8::1") is None


def test_an_empty_home_has_nothing_and_no_primary_gateway() -> None:
    home = parse_home({})
    assert (home.home_id, home.gateways, dict(home.shades), home.scenes) == (None, (), {}, ())
    assert home.primary_gateway is None


def test_the_home_id_may_come_from_the_home_object() -> None:
    assert parse_home({"home": {"key": "abc"}}).home_id == "abc"


def test_entries_without_an_id_are_left_out() -> None:
    home = parse_home({
        "gateways": [{"name": "no serial"}, "junk"],
        "rooms": [{"name": "Room", "shades": [{"ptName": "no id"}, {"id": 1}]}, 7],
        "scenes": [{"name": "no id"}, {"_id": 2}],
    })
    assert home.gateways == ()
    assert list(home.shades) == [1]
    assert home.shades[1].name == "Shade 1" and home.shades[1].room_name == "Room"
    assert [scene.name for scene in home.scenes] == ["Scene 2"]


def test_a_shade_without_a_name_takes_its_bluetooth_name() -> None:
    assert parse_shade({"id": 3, "bleName": "DU1:0003"}).name == "DU1:0003"


def test_a_shade_without_an_id_is_refused() -> None:
    with pytest.raises(ValueError, match="without an id"):
        parse_shade({"ptName": "x"})


def test_capabilities_not_known_have_no_name() -> None:
    assert parse_shade({"id": 1, "capabilities": 42}).capability is None


@pytest.mark.parametrize("firmware", [None, "3.0.433", {"revision": 3, "build": 1}])
def test_firmware_not_given_in_parts_is_none(firmware: object) -> None:
    assert parse_shade({"id": 1, "firmware": firmware}).firmware is None


def test_positions_are_limited_to_0_to_1_and_anything_else_is_left_out() -> None:
    assert parse_positions({"primary": 1.2, "secondary": -0.1, "tilt": True, "velocity": 0}) == {
        Rail.PRIMARY: 1.0, Rail.SECONDARY: 0.0}
    assert parse_positions(None) == {}


def test_a_command_the_gateway_took_has_no_errors() -> None:
    assert command_errors(COMMAND_RESPONSE) == []
    assert command_errors(None) == []


def test_errors_of_the_gateway_and_of_each_shade_are_named() -> None:
    assert command_errors({"err": 3, "responses": [
        {"bleName": "DU1:0154", "err": 7, "id": 154}, {"id": 155, "err": 0}]}) == [
        "gateway err=3", "shade 154 (DU1:0154) err=7"]
