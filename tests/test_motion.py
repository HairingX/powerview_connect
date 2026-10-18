"""Following a shade's motion, with the timings a real gateway and shade were measured at."""
from captured import POLLS, SSE
from powerview_connect import Rail
from powerview_connect._model import ShadeEvent, parse_event
from powerview_connect._motion import ShadeTracker

P, S, T = Rail.PRIMARY, Rail.SECONDARY, Rail.TILT


def _events() -> list[ShadeEvent]:
    return [event for line in SSE.splitlines() if (event := parse_event(line)) is not None]


def test_the_events_alone_start_and_end_a_move() -> None:
    tracker = ShadeTracker({P: 0.0, S: 0.71, T: 0.0})
    _, started, stopped = _events()
    tracker.motion_started(started, now=0.0)
    assert tracker.moving and tracker.eta == 10
    assert (tracker.direction(S), tracker.direction(P)) == (-1, 0)
    tracker.motion_stopped(stopped)
    assert not tracker.moving and tracker.eta is None
    assert tracker.current[S] == 0.5
    assert tracker.direction(S) == 0


def test_a_move_ends_once_the_target_is_reached_and_still() -> None:
    tracker = ShadeTracker({P: 0.18, S: 0.5, T: 0.0})
    tracker.motion_started(ShadeEvent("motion-started", 154, {P: 0.18, S: 0.5, T: 0.0},
                                      {P: 0.0, S: 0.5, T: 0.0}, 9), now=0.0)
    ended_at = None
    for at, primary in POLLS:
        moving = tracker.poll({P: primary, S: 0.5, T: 0.0}, now=at)
        if at < 19.5:
            assert moving, at
            assert tracker.direction(P) == -1
        if not moving:
            ended_at = at
            break
    # 0.0 was reached at 19.5 s; the first poll 1.5 s still after that ends it.
    assert ended_at == 21.4
    assert tracker.current[P] == 0.0


def test_the_seconds_before_the_motor_starts_do_not_end_a_move() -> None:
    tracker = ShadeTracker({P: 0.18, S: 0.5})
    assert tracker.command(0.0, {P: 0.0})
    for at in (1.0, 2.0, 3.0, 4.0):
        assert tracker.poll({P: 0.18, S: 0.5}, now=at)


def test_a_move_the_shade_stops_short_of_its_target_ends_when_it_stalls() -> None:
    """The shade's firmware stops the top rail where it meets the bottom rail."""
    tracker = ShadeTracker({P: 0.5, S: 0.0, T: 0.0})
    tracker.command(0.0, {S: 0.6})
    tracker.poll({P: 0.5, S: 0.2}, now=6.0)
    tracker.poll({P: 0.5, S: 0.5}, now=12.0)
    assert tracker.poll({P: 0.5, S: 0.5}, now=15.0)
    assert not tracker.poll({P: 0.5, S: 0.5}, now=18.5)
    assert tracker.current[S] == 0.5


def test_a_command_to_where_the_shade_is_expects_no_move() -> None:
    tracker = ShadeTracker({P: 0.2, S: 0.5})
    assert not tracker.command(0.0, {P: 0.2})
    assert not tracker.moving


def test_a_command_to_a_rail_never_reported_expects_a_move() -> None:
    assert ShadeTracker().command(0.0, {P: 0.2})


def test_a_command_to_the_other_rail_during_a_move_keeps_the_first_target() -> None:
    tracker = ShadeTracker({P: 0.0, S: 0.71})
    tracker.command(0.0, {P: 0.2})
    tracker.command(0.5, {S: 0.5})
    assert (tracker.target[P], tracker.target[S]) == (0.2, 0.5)
    assert (tracker.direction(P), tracker.direction(S)) == (1, -1)


def test_a_late_motion_stopped_after_the_move_ended_changes_nothing() -> None:
    tracker = ShadeTracker({P: 0.0, S: 0.71})
    tracker.command(0.0, {S: 0.5})
    tracker.poll({S: 0.6}, now=6.0)
    tracker.poll({S: 0.5}, now=12.0)
    assert not tracker.poll({S: 0.5}, now=14.0)
    tracker.motion_stopped(ShadeEvent("motion-stopped", 154, {P: 0.0, S: 0.5, T: 0.0}, {}, None))
    assert not tracker.moving and tracker.current[S] == 0.5


def test_positions_read_while_nothing_moves_are_taken_and_ignored_during_a_move() -> None:
    tracker = ShadeTracker({P: 0.0})
    assert tracker.snapshot({P: 0.1})
    assert not tracker.snapshot({P: 0.1})
    tracker.command(0.0, {P: 0.5})
    assert not tracker.snapshot({P: 0.0})
    tracker.poll({P: 0.2}, now=5.0)
    assert tracker.current[P] == 0.2


def test_a_poll_while_nothing_moves_is_ignored() -> None:
    tracker = ShadeTracker({P: 0.0})
    assert not tracker.poll({P: 0.3}, now=1.0)
    assert tracker.current[P] == 0.0


def test_a_command_that_never_moves_the_shade_ends_after_the_grace_time() -> None:
    tracker = ShadeTracker({P: 0.0})
    tracker.command(0.0, {P: 0.5})
    assert tracker.poll({P: 0.0}, now=24.9)
    assert not tracker.poll({P: 0.0}, now=25.0)


def test_a_move_that_keeps_creeping_ends_at_the_timeout() -> None:
    tracker = ShadeTracker({P: 0.0})
    tracker.command(0.0, {P: 1.0})
    for second in range(1, 90):
        assert tracker.poll({P: second / 100}, now=float(second))
    assert not tracker.poll({P: 0.9}, now=90.0)


def test_the_timeout_grows_with_the_gateways_estimate() -> None:
    """Three times the estimate, plus 30 s: 120 s for an estimate of 30 s."""
    tracker = ShadeTracker({P: 0.0})
    tracker.motion_started(ShadeEvent("motion-started", 1, {P: 0.0}, {P: 1.0}, 30.0), now=0.0)
    for second in range(1, 120):
        assert tracker.poll({P: second / 200}, now=float(second))
    assert not tracker.poll({P: 0.6}, now=120.0)


def test_after_a_stop_the_direction_is_that_of_the_last_change() -> None:
    tracker = ShadeTracker({P: 0.5, S: 0.2})
    tracker.command(0.0, {P: 0.9, S: 0.0})
    tracker.poll({P: 0.6, S: 0.1}, now=5.0)
    tracker.stop_requested(now=5.5)
    assert tracker.moving and tracker.target[P] is None
    assert (tracker.direction(P), tracker.direction(S), tracker.direction(T)) == (1, -1, 0)
    assert not tracker.poll({P: 0.6, S: 0.1}, now=11.5)


def test_a_stop_while_nothing_moves_changes_nothing() -> None:
    tracker = ShadeTracker({P: 0.5})
    tracker.stop_requested(now=1.0)
    assert not tracker.moving


def test_a_rail_at_its_target_does_not_move() -> None:
    tracker = ShadeTracker({P: 0.5, S: 0.2})
    tracker.command(0.0, {P: 0.9, S: 0.2})
    assert tracker.direction(S) == 0


def test_the_targets_of_a_move_are_kept_once_it_ends_until_the_next_begins() -> None:
    tracker = ShadeTracker({P: 0.0, S: 0.0, T: 0.0})
    tracker.motion_started(ShadeEvent("motion-started", 1, {P: 0.0}, {P: 1.0, S: 0.0}, 9),
                           now=0.0)
    assert tracker.last_target == {}
    tracker.motion_stopped(ShadeEvent("motion-stopped", 1, {P: 0.9}, {}, None))
    assert tracker.last_target == {P: 1.0, S: 0.0}
    tracker.command(1.0, {P: 0.5})
    assert tracker.last_target == {}


def test_a_motor_backing_off_after_the_move_ended_keeps_the_targets_of_the_move() -> None:
    """As at the top: the move ends there, and the motor backs off by itself after it."""
    tracker = ShadeTracker({P: 0.0, S: 0.0, T: 0.0})
    tracker.motion_started(ShadeEvent("motion-started", 1, {P: 0.0}, {P: 1.0}, 9), now=0.0)
    tracker.motion_stopped(ShadeEvent("motion-stopped", 1, {P: 1.0}, {}, None))
    tracker.motion_stopped(ShadeEvent("motion-stopped", 1, {P: 0.95}, {}, None))
    assert (tracker.last_target, tracker.current[P]) == ({P: 1.0}, 0.95)


def test_the_first_motion_stopped_ends_a_move_the_tracker_ended_already() -> None:
    tracker = ShadeTracker({P: 0.0, S: 0.0, T: 0.0}, stall=6.0)
    tracker.command(0.0, {P: 1.0})
    tracker.poll({P: 0.5}, 1.0)
    assert not tracker.poll({P: 0.5}, 8.0)
    tracker.motion_stopped(ShadeEvent("motion-stopped", 1, {P: 0.5}, {}, None))
    assert tracker.last_target == {P: 1.0}


def test_a_stop_sent_forgets_the_targets_of_the_move() -> None:
    tracker = ShadeTracker({P: 0.0, S: 0.0, T: 0.0})
    tracker.command(0.0, {P: 1.0})
    tracker.stop_requested(1.0)
    tracker.motion_stopped(ShadeEvent("motion-stopped", 1, {P: 0.9}, {}, None))
    assert tracker.last_target == {}
    tracker.command(2.0, {P: 0.2})
    tracker.motion_stopped(ShadeEvent("motion-stopped", 1, {P: 0.2}, {}, None))
    tracker.stop_requested(3.0)
    assert tracker.last_target == {}
