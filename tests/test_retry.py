"""Telling a cover whose opening stalled short of fully open, and the settings of its retry."""
from typing import Any, cast

import pytest

from powerview_connect import Capabilities, CoverRole, CoverSpec, Rail, Shade, StalledOpenRetry
from powerview_connect._covers import covers
from powerview_connect._retry import stalled_openness

P, S, T = Rail.PRIMARY, Rail.SECONDARY, Rail.TILT
SETTINGS = StalledOpenRetry()


def _covers(capabilities: int, shade_type: int | None = 6) -> tuple[CoverSpec, ...]:
    return covers(Shade(id=1, name="Shade", type=shade_type, capabilities=capabilities,
                        power_type=1, room_id=None, room_name=None, firmware=None, ble_name=None,
                        serial=None))


CURTAIN = _covers(Capabilities.BOTTOM_UP)
TDBU = _covers(Capabilities.TOP_DOWN_BOTTOM_UP, 8)


def stalled_cover(
    shade_covers: tuple[CoverSpec, ...], target: dict[Rail, float], final: dict[Rail, float],
    settings: StalledOpenRetry,
) -> tuple[CoverSpec, float] | None:
    """The first of `shade_covers` that stalled, and how far it is open."""
    for cover in shade_covers:
        if (openness := stalled_openness(cover, target, final, settings)) is not None:
            return cover, openness
    return None


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
    top_down = _covers(Capabilities.TOP_DOWN, 7)
    assert stalled_cover(top_down, {P: 0.0}, {P: 0.1}, SETTINGS) == (top_down[0], 0.9)
    assert stalled_cover(top_down, {P: 1.0}, {P: 0.9}, SETTINGS) is None


def test_a_lower_lowest_stuck_openness_counts_lower_stops() -> None:
    assert stalled_cover(CURTAIN, {P: 1.0}, {P: 0.5}, StalledOpenRetry(min_openness=0.5)) is not None


@pytest.mark.parametrize(("min_openness", "lower_by", "attempts"), [
    (-0.1, 0.1, 2), (1.0, 0.1, 2), (0.8, 0.0, 2), (0.8, 1.1, 2), (0.8, 0.1, 0)])
def test_retry_settings_that_cannot_work_are_refused(
    min_openness: float, lower_by: float, attempts: int
) -> None:
    with pytest.raises(ValueError):
        StalledOpenRetry(min_openness=min_openness, lower_by=lower_by, attempts=attempts)


def test_a_retry_lowers_a_tenth_and_tries_twice_unless_told_otherwise() -> None:
    assert StalledOpenRetry() == StalledOpenRetry(min_openness=0.8, lower_by=0.1, attempts=2)
    assert StalledOpenRetry(min_openness=0.0, lower_by=1.0, attempts=1).attempts == 1


def test_only_pleated_covers_that_open_upwards_are_retryable() -> None:
    assert [cover.retryable for cover in TDBU] == [False, True]
    assert CURTAIN[0].retryable and _covers(Capabilities.TOP_DOWN, 7)[0].retryable
    assert not any(cover.retryable for cover in _covers(Capabilities.DUAL_OVERLAPPED, 6))
    assert not _covers(Capabilities.TILT_ONLY_180, 6)[0].retryable


@pytest.mark.parametrize("shade_type", [1, 42, None])
def test_a_shade_whose_fabric_does_not_gather_is_not_retryable(shade_type: int | None) -> None:
    assert not _covers(Capabilities.BOTTOM_UP, shade_type)[0].retryable


def test_retry_settings_are_given_by_name() -> None:
    with pytest.raises(TypeError):
        cast(Any, StalledOpenRetry)(0.8)
