"""Telling a cover that stopped short of fully open, and the steps that raise it."""
import pytest

from powerview_connect import Capabilities, CoverRole, CoverSpec, Rail, Shade, StallRecovery
from powerview_connect._covers import covers
from powerview_connect._recovery import recovery_steps, stalled_cover

P, S, T = Rail.PRIMARY, Rail.SECONDARY, Rail.TILT
SETTINGS = StallRecovery()


def _covers(capabilities: int) -> tuple[CoverSpec, ...]:
    return covers(Shade(id=1, name="Shade", type=None, capabilities=capabilities, power_type=1,
                        room_id=None, room_name=None, firmware=None, ble_name=None, serial=None))


CURTAIN = _covers(Capabilities.BOTTOM_UP)
TDBU = _covers(Capabilities.TOP_DOWN_BOTTOM_UP)


def test_a_curtain_sent_fully_open_that_stopped_short_is_stalled() -> None:
    assert stalled_cover(CURTAIN, {P: 1.0}, {P: 0.9}, SETTINGS) == (CURTAIN[0], 0.9)


@pytest.mark.parametrize(("reached", "stalled"), [
    (0.79, False), (0.8, True), (0.99, True), (0.995, False), (1.0, False)])
def test_only_a_stop_from_the_lowest_stuck_openness_to_just_short_of_open_is_a_stall(
    reached: float, stalled: bool
) -> None:
    assert (stalled_cover(CURTAIN, {P: 1.0}, {P: reached}, SETTINGS) is not None) is stalled


def test_a_move_not_sent_fully_open_or_without_a_target_is_no_stall() -> None:
    assert stalled_cover(CURTAIN, {P: 0.9}, {P: 0.85}, SETTINGS) is None
    assert stalled_cover(CURTAIN, {}, {P: 0.85}, SETTINGS) is None
    assert stalled_cover(CURTAIN, {P: 1.0}, {}, SETTINGS) is None


def test_the_bottom_rail_stalls_only_while_the_top_rail_is_at_the_top() -> None:
    bottom = next(cover for cover in TDBU if cover.role is CoverRole.BOTTOM)
    assert stalled_cover(TDBU, {P: 1.0, S: 0.0}, {P: 0.9, S: 0.0}, SETTINGS) == (bottom, 0.9)
    assert stalled_cover(TDBU, {P: 1.0, S: 0.0}, {P: 0.9, S: 0.1}, SETTINGS) is None


def test_the_top_rail_of_a_top_down_bottom_up_shade_never_stalls() -> None:
    assert stalled_cover(TDBU, {P: 0.0, S: 0.0}, {P: 0.0, S: 0.1}, SETTINGS) is None


@pytest.mark.parametrize("capabilities", [
    Capabilities.DUAL_OVERLAPPED, Capabilities.TILT_ONLY_180])
def test_front_rear_and_vanes_alone_never_stall(capabilities: int) -> None:
    assert stalled_cover(_covers(capabilities), {P: 1.0, S: 1.0, T: 1.0},
                         {P: 0.9, S: 0.9, T: 0.9}, SETTINGS) is None


def test_an_inverted_cover_is_judged_by_how_far_it_is_open() -> None:
    top_down = _covers(Capabilities.TOP_DOWN)
    assert stalled_cover(top_down, {P: 0.0}, {P: 0.1}, SETTINGS) == (top_down[0], 0.9)
    assert stalled_cover(top_down, {P: 1.0}, {P: 0.9}, SETTINGS) is None


def test_a_lower_lowest_stuck_openness_counts_lower_stops() -> None:
    assert stalled_cover(CURTAIN, {P: 1.0}, {P: 0.5}, StallRecovery(min_openness=0.5)) is not None


def test_the_steps_split_what_is_left_evenly_and_end_fully_open() -> None:
    assert recovery_steps(0.9, 3) == [0.93, 0.97, 1.0]
    assert recovery_steps(0.8, 2) == [0.9, 1.0]
    assert recovery_steps(0.85, 1) == [1.0]


def test_steps_that_would_not_move_the_cover_are_left_out() -> None:
    assert recovery_steps(0.99, 3) == [1.0]


@pytest.mark.parametrize(("min_openness", "steps", "pause"), [
    (-0.1, 3, 10.0), (1.0, 3, 10.0), (0.8, 0, 10.0), (0.8, 3, -1.0)])
def test_stall_recovery_settings_that_cannot_work_are_refused(
    min_openness: float, steps: int, pause: float
) -> None:
    with pytest.raises(ValueError):
        StallRecovery(min_openness, steps, pause)
    assert StallRecovery(0.0, 1, 0.0).steps == 1
