from datetime import datetime, timedelta, timezone

from app.models import EntrySource
from app.processors.track import LapPoint, ProcessedTrack, TrackPoint
from app.processors.merge import build_log_entries


def _ts(offset_s: int) -> datetime:
    return datetime(2026, 6, 20, 8, 0, 0, tzinfo=timezone.utc) + timedelta(seconds=offset_s)


def _tp(offset_s: int, dist_nm: float = 0.0) -> TrackPoint:
    return TrackPoint(
        timestamp=_ts(offset_s),
        lat=44.0, lon=15.0,
        speed_knots=5.0, course=90.0,
        air_temperature=None,
        distance_nm=dist_nm,
        raw_distance_nm=None,
    )


def _lap(offset_s: int) -> LapPoint:
    return LapPoint(timestamp=_ts(offset_s), lat=44.1, lon=15.1)


# --- empty track ---

def test_empty_track_returns_empty():
    track = ProcessedTrack(track_points=[], turning_points=[], hourly_points=[])
    result = build_log_entries(1, track, [])
    assert result == []


# --- turning point near end not dropped ---

def test_turning_point_30s_before_end_is_kept():
    """Turning point 30 s before final track point must not be deduplicated away."""
    pts = [_tp(0, 0.0), _tp(600, 5.0), _tp(1200, 10.0), _tp(1800, 15.0)]
    tp_near_end = _tp(1770, 14.8)  # 30 s before last point
    track = ProcessedTrack(
        track_points=pts,
        turning_points=[tp_near_end],
        hourly_points=[],
    )
    entries = build_log_entries(1, track, [])
    sources = [e.source for e in entries]
    assert EntrySource.turning_point in sources, "Turning point 30 s before end should be kept"


def test_turning_point_90s_before_end_is_kept():
    """Turning point 90 s before end (outside 60 s window) must be kept."""
    pts = [_tp(0, 0.0), _tp(600, 5.0), _tp(1200, 10.0), _tp(1800, 15.0)]
    tp = _tp(1710, 14.5)  # 90 s before last point
    track = ProcessedTrack(track_points=pts, turning_points=[tp], hourly_points=[])
    entries = build_log_entries(1, track, [])
    sources = [e.source for e in entries]
    assert EntrySource.turning_point in sources


# --- start and end anchors always present ---

def test_start_and_end_anchors_present():
    pts = [_tp(0, 0.0), _tp(3600, 20.0)]
    track = ProcessedTrack(track_points=pts, turning_points=[], hourly_points=[])
    entries = build_log_entries(1, track, [])
    manual = [e for e in entries if e.source == EntrySource.manual]
    assert len(manual) == 2
    timestamps = {e.timestamp for e in manual}
    assert _ts(0) in timestamps
    assert _ts(3600) in timestamps


# --- lap points ---

def test_lap_point_appears_with_lap_source():
    pts = [_tp(0, 0.0), _tp(1800, 10.0), _tp(3600, 20.0)]
    lap = _lap(900)
    track = ProcessedTrack(track_points=pts, turning_points=[], hourly_points=[])
    entries = build_log_entries(1, track, [lap])
    sources = [e.source for e in entries]
    assert EntrySource.lap in sources


# --- dedup: exact duplicate timestamps rejected ---

def test_exact_duplicate_timestamps_deduplicated():
    pts = [_tp(0, 0.0), _tp(1800, 10.0)]
    tp = _tp(1800, 10.0)  # same timestamp as last point
    track = ProcessedTrack(track_points=pts, turning_points=[tp], hourly_points=[])
    entries = build_log_entries(1, track, [])
    # Should have exactly 2 entries (start + end), not 3
    assert len(entries) == 2


# --- entries are sorted by timestamp ---

def test_entries_sorted_by_timestamp():
    pts = [_tp(0, 0.0), _tp(1800, 10.0), _tp(3600, 20.0)]
    tp = _tp(900, 5.0)
    track = ProcessedTrack(track_points=pts, turning_points=[tp], hourly_points=[])
    entries = build_log_entries(1, track, [])
    times = [e.timestamp for e in entries]
    assert times == sorted(times)
