"""The covers each kind of shade is controlled as, and how far each is open."""
import pytest

from powerview_connect import Capabilities, CoverRole, CoverSpec, Rail, Shade
from powerview_connect._covers import covers

P, S, T = Rail.PRIMARY, Rail.SECONDARY, Rail.TILT


def _shade(capabilities: int) -> Shade:
    return Shade(id=1, name="Shade", type=None, capabilities=capabilities, power_type=1,
                 room_id=None, room_name=None, firmware=None, ble_name=None, serial=None)


@pytest.mark.parametrize(("capabilities", "expected"), [
    (Capabilities.BOTTOM_UP, [CoverSpec(CoverRole.SHADE, P)]),
    (Capabilities.BOTTOM_UP_TILT_90, [CoverSpec(CoverRole.SHADE, P, tilt=True)]),
    (Capabilities.BOTTOM_UP_TILT_180, [CoverSpec(CoverRole.SHADE, P, tilt=True)]),
    (Capabilities.VERTICAL, [CoverSpec(CoverRole.SHADE, P, vertical=True)]),
    (Capabilities.VERTICAL_TILT_180, [CoverSpec(CoverRole.SHADE, P, tilt=True, vertical=True)]),
    (Capabilities.TILT_ONLY_180, [CoverSpec(CoverRole.VANES, None)]),
    (Capabilities.TOP_DOWN, [CoverSpec(CoverRole.SHADE, P, inverted=True)]),
    (Capabilities.TOP_DOWN_BOTTOM_UP, [CoverSpec(CoverRole.TOP, S), CoverSpec(CoverRole.BOTTOM, P)]),
    (Capabilities.DUAL_OVERLAPPED, [CoverSpec(CoverRole.FRONT, P), CoverSpec(CoverRole.REAR, S)]),
    (Capabilities.DUAL_OVERLAPPED_TILT_90,
     [CoverSpec(CoverRole.FRONT, P, tilt=True), CoverSpec(CoverRole.REAR, S)]),
    (Capabilities.DUAL_OVERLAPPED_TILT_180,
     [CoverSpec(CoverRole.FRONT, P, tilt=True), CoverSpec(CoverRole.REAR, S)]),
    (42, [CoverSpec(CoverRole.SHADE, P)]),
])
def test_each_kind_of_shade_has_its_covers(capabilities: int, expected: list[CoverSpec]) -> None:
    assert list(covers(_shade(capabilities))) == expected


def test_a_cover_opens_by_its_rail_and_an_inverted_one_the_other_way() -> None:
    cover, inverted = CoverSpec(CoverRole.SHADE, P), CoverSpec(CoverRole.SHADE, P, inverted=True)
    assert (cover.openness({P: 0.25}), inverted.openness({P: 0.25})) == (0.25, 0.75)
    assert (cover.position(0.6), inverted.position(0.6)) == ({P: 0.6}, {P: 0.4})
    assert cover.openness({}) is None and CoverSpec(CoverRole.VANES, None).openness({T: 1.0}) is None
    assert (cover.direction({P: 1}), inverted.direction({P: 1}), cover.direction({})) == (1, -1, 0)


def test_a_cover_is_closed_at_zero_and_vanes_alone_by_their_tilt() -> None:
    cover, vanes = CoverSpec(CoverRole.SHADE, P, tilt=True), CoverSpec(CoverRole.VANES, None)
    assert (cover.closed({P: 0.004, T: 1.0}), cover.closed({P: 0.5})) == (True, False)
    assert (vanes.closed({T: 0.0}), vanes.closed({T: 0.3}), vanes.closed({})) == (True, False, None)
    assert (cover.tilt_openness({T: 0.3}), vanes.tilt_openness({T: 0.3})) == (0.3, 0.3)
    assert CoverSpec(CoverRole.SHADE, P).tilt_openness({T: 0.3}) is None
    assert vanes.direction({T: -1}) == -1
    assert (cover.tilt_position(0.2), vanes.tilt_position(0.2)) == ({T: 0.2}, {T: 0.2})


def test_a_cover_refuses_what_it_cannot_do() -> None:
    with pytest.raises(ValueError, match="no rail"):
        CoverSpec(CoverRole.VANES, None).position(0.5)
    with pytest.raises(ValueError, match="does not tilt"):
        CoverSpec(CoverRole.SHADE, P).tilt_position(0.5)
    for openness in (-0.1, 1.1):
        with pytest.raises(ValueError, match="0.0 to 1.0"):
            CoverSpec(CoverRole.SHADE, P).position(openness)


def test_capabilities_are_named_and_a_number_not_known_too() -> None:
    assert _shade(Capabilities.TOP_DOWN_BOTTOM_UP).capability_name == "Top Down Bottom Up"
    assert _shade(42).capability_name == "Capabilities 42"
