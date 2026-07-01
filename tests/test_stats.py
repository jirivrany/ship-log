from datetime import datetime, timezone

from app.models import EntrySource, LogEntry, PropulsionType
from app.stats import _format_hhmm, compute_stats


def _entry(leg_id=1, minutes_offset=0, log_value=None, propulsion=None, source=EntrySource.manual):
    ts = datetime(2026, 6, 20, 8, 0, 0, tzinfo=timezone.utc)
    from datetime import timedelta
    ts = ts + timedelta(minutes=minutes_offset)
    e = LogEntry(
        leg_id=leg_id,
        timestamp=ts,
        lat=44.0,
        lon=15.0,
        source=source,
        log_value=log_value,
        propulsion=PropulsionType(propulsion) if propulsion else None,
    )
    return e


# --- _format_hhmm ---

def test_format_hhmm_whole_hours():
    assert _format_hhmm(120.0) == "2:00"

def test_format_hhmm_mixed():
    assert _format_hhmm(75.0) == "1:15"

def test_format_hhmm_zero():
    assert _format_hhmm(0.0) == "0:00"

def test_format_hhmm_sub_hour():
    assert _format_hhmm(45.0) == "0:45"


# --- compute_stats ---

def test_empty_entries():
    s = compute_stats([])
    assert s["total_nm"] == 0.0
    assert s["entry_count"] == 0
    assert s["duration_hhmm"] == "0:00"

def test_single_entry():
    s = compute_stats([_entry(log_value=5.0, propulsion="sail")])
    assert s["total_nm"] == 0.0
    assert s["entry_count"] == 1

def test_all_none_propulsion_goes_to_unknown():
    entries = [
        _entry(minutes_offset=0,  log_value=0.0),
        _entry(minutes_offset=60, log_value=10.0),
    ]
    s = compute_stats(entries)
    assert s["unknown_nm"] == 10.0
    assert s["motor_nm"] == 0.0
    assert s["total_nm"] == 10.0

def test_mixed_propulsion_buckets():
    # propulsion key is taken from PREV entry in each segment
    # seg 0→1: prev=motor, dist=10  → motor=10
    # seg 1→2: prev=sail,  dist=15  → sail=15
    # seg 2→3: prev=sail,  dist=5   → sail+=5 → sail=20
    # seg 3 has no propulsion (unknown), but there's no seg 3→4, so unknown=0
    entries = [
        _entry(minutes_offset=0,   log_value=0.0,  propulsion="motor"),
        _entry(minutes_offset=60,  log_value=10.0, propulsion="sail"),
        _entry(minutes_offset=120, log_value=25.0, propulsion="sail"),
        _entry(minutes_offset=180, log_value=30.0),
    ]
    s = compute_stats(entries)
    assert s["motor_nm"] == 10.0
    assert s["sail_nm"] == 20.0
    assert s["unknown_nm"] == 0.0
    assert s["total_nm"] == 30.0

def test_cross_leg_boundary_negative_dist_skipped():
    # Simulate two legs concatenated: leg1 ends at 45 Nm, leg2 starts at 2 Nm
    entries = [
        _entry(leg_id=1, minutes_offset=0,   log_value=0.0,  propulsion="sail"),
        _entry(leg_id=1, minutes_offset=120, log_value=45.0, propulsion="sail"),
        _entry(leg_id=2, minutes_offset=200, log_value=2.0,  propulsion="motor"),
        _entry(leg_id=2, minutes_offset=260, log_value=12.0, propulsion="motor"),
    ]
    s = compute_stats(entries)
    assert s["sail_nm"] == 45.0
    assert s["motor_nm"] == 10.0
    assert s["total_nm"] == 55.0

def test_wall_clock_duration():
    entries = [
        _entry(minutes_offset=0,   log_value=0.0,  propulsion="sail"),
        _entry(minutes_offset=30,  log_value=5.0),   # no propulsion — gap not in min_by_prop
        _entry(minutes_offset=120, log_value=20.0, propulsion="sail"),
    ]
    s = compute_stats(entries)
    # wall-clock: 120 minutes (first to last)
    assert s["duration_hhmm"] == "2:00"

def test_entry_count_and_lap_count():
    entries = [
        _entry(minutes_offset=0,   log_value=0.0,  propulsion="sail"),
        _entry(minutes_offset=30,  log_value=5.0,  source=EntrySource.lap),
        _entry(minutes_offset=60,  log_value=10.0, propulsion="sail"),
    ]
    s = compute_stats(entries)
    assert s["entry_count"] == 3
    assert s["lap_count"] == 1

def test_dist_le_zero_skipped():
    entries = [
        _entry(minutes_offset=0,  log_value=10.0, propulsion="motor"),
        _entry(minutes_offset=30, log_value=10.0, propulsion="motor"),  # dist=0, skip
        _entry(minutes_offset=60, log_value=5.0,  propulsion="motor"),  # dist<0, skip
    ]
    s = compute_stats(entries)
    assert s["motor_nm"] == 0.0
    assert s["total_nm"] == 0.0
