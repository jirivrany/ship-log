"""Tests for the source-neutral process_track() core."""
from datetime import datetime, timedelta, timezone

from app.processors.track import RawPoint, process_track

# ~5 knots northward: 2.5722 m/s * 30 s = 77.17 m ≈ 0.000694° latitude per sample
LAT_STEP_5KN = 0.000694


def _ts(offset_s: int) -> datetime:
    return datetime(2026, 6, 20, 8, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)


def _points_northward(n: int, speed_knots=None, step_s: int = 30) -> list[RawPoint]:
    return [
        RawPoint(
            timestamp=_ts(i * step_s),
            lat=44.0 + i * LAT_STEP_5KN * (step_s / 30),
            lon=15.0,
            speed_knots=speed_knots,
        )
        for i in range(n)
    ]


def test_empty_input():
    result = process_track([])
    assert result.track_points == []
    assert result.turning_points == []
    assert result.hourly_points == []


def test_explicit_speed_passed_through():
    pts = _points_northward(4, speed_knots=5.678)
    result = process_track(pts)
    assert all(tp.speed_knots == 5.68 for tp in result.track_points)


def test_missing_speed_derived_from_positions():
    """GPX points carry no speed field — it must come from position deltas."""
    pts = _points_northward(5, speed_knots=None)
    result = process_track(pts)
    for tp in result.track_points:
        assert 4.5 < tp.speed_knots < 5.5, tp


def test_first_point_speed_backfilled_from_second():
    pts = _points_northward(3, speed_knots=None)
    result = process_track(pts)
    assert result.track_points[0].speed_knots == result.track_points[1].speed_knots


def test_sampling_every_30s():
    # one point every 10 s for 5 minutes -> sampled roughly every 30 s
    pts = _points_northward(31, speed_knots=5.0, step_s=10)
    result = process_track(pts)
    times = [tp.timestamp for tp in result.track_points]
    assert all(
        (b - a).total_seconds() >= 30 for a, b in zip(times, times[1:])
    )
    assert len(times) == 11  # 0s, 30s, ..., 300s


def test_unsorted_input_is_sorted():
    pts = _points_northward(4, speed_knots=5.0)
    result = process_track(list(reversed(pts)))
    times = [tp.timestamp for tp in result.track_points]
    assert times == sorted(times)


def test_source_distance_converted_to_nm():
    pts = _points_northward(3, speed_knots=5.0)
    pts[2].dist_m = 1852.0
    result = process_track(pts)
    assert result.track_points[0].raw_distance_nm is None
    assert result.track_points[2].raw_distance_nm == 1.0


def test_course_is_northward():
    pts = _points_northward(4, speed_knots=5.0)
    result = process_track(pts)
    # all but the last computed from the following point; last copies previous
    for tp in result.track_points:
        assert tp.course in (0.0, 360.0) or abs(tp.course) < 1.0


def test_hourly_points():
    pts = _points_northward(300, speed_knots=5.0)  # 300 samples × 30 s = 2.5 h
    result = process_track(pts)
    assert len(result.hourly_points) == 2
    elapsed = [
        (hp.timestamp - result.track_points[0].timestamp).total_seconds()
        for hp in result.hourly_points
    ]
    assert elapsed[0] >= 3600 and elapsed[1] >= 7200
