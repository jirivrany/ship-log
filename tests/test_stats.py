from datetime import datetime, timezone

import pytest

from app.models import EntrySource, LogEntry, PropulsionType
from app.stats import _format_hhmm, aggregate_stats, compute_stats


def _entry(leg_id=1, minutes_offset=0, log_value=None, propulsion="motor", source=EntrySource.manual):
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
        propulsion=PropulsionType(propulsion),
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

def test_all_motor_propulsion_by_default():
    entries = [
        _entry(minutes_offset=0,  log_value=0.0),
        _entry(minutes_offset=60, log_value=10.0),
    ]
    s = compute_stats(entries)
    assert s["motor_nm"] == 10.0
    assert s["total_nm"] == 10.0
    assert "unknown_nm" not in s

def test_mixed_propulsion_buckets():
    # propulsion key is taken from PREV entry in each segment
    # seg 0→1: prev=motor, dist=10  → motor=10
    # seg 1→2: prev=sail,  dist=15  → sail=15
    # seg 2→3: prev=sail,  dist=5   → sail+=5 → sail=20
    entries = [
        _entry(minutes_offset=0,   log_value=0.0,  propulsion="motor"),
        _entry(minutes_offset=60,  log_value=10.0, propulsion="sail"),
        _entry(minutes_offset=120, log_value=25.0, propulsion="sail"),
        _entry(minutes_offset=180, log_value=30.0, propulsion="sail"),
    ]
    s = compute_stats(entries)
    assert s["motor_nm"] == 10.0
    assert s["sail_nm"] == 20.0
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
        _entry(minutes_offset=30,  log_value=5.0,  propulsion="motor"),
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


# --- raw values ---

def test_raw_values_unrounded_and_consistent():
    entries = [
        _entry(minutes_offset=0,  log_value=0.0,   propulsion="sail"),
        _entry(minutes_offset=45, log_value=5.55,  propulsion="motor"),
        _entry(minutes_offset=90, log_value=10.11, propulsion="motor"),
    ]
    s = compute_stats(entries)
    assert s["raw"]["sail_nm"] == 5.55           # unrounded
    assert s["raw"]["motor_nm"] == pytest.approx(10.11 - 5.55)
    assert s["raw"]["total_nm"] == pytest.approx(10.11)
    assert s["sail_nm"] == round(s["raw"]["sail_nm"], 1)
    assert s["raw"]["sail_min"] == 45.0
    assert s["raw"]["motor_min"] == 45.0
    assert s["raw"]["duration_min"] == 90.0      # wall clock


def test_raw_anchor_minutes_exposed():
    # anchor has no formatted _hhmm key, but raw minutes must not be dropped
    entries = [
        _entry(minutes_offset=0,  log_value=0.0, propulsion="anchor"),
        _entry(minutes_offset=30, log_value=0.5, propulsion="motor"),
    ]
    s = compute_stats(entries)
    assert s["raw"]["anchor_nm"] == 0.5
    assert s["raw"]["anchor_min"] == 30.0


# --- aggregate_stats ---

def test_aggregate_stats_sums_raw_then_formats():
    v1 = compute_stats([
        _entry(minutes_offset=0,  log_value=0.0,  propulsion="sail"),
        _entry(minutes_offset=90, log_value=10.25, propulsion="sail"),
    ])
    v2 = compute_stats([
        _entry(minutes_offset=0,  log_value=0.0,  propulsion="motor"),
        _entry(minutes_offset=60, log_value=5.25, propulsion="sail"),
        _entry(minutes_offset=90, log_value=8.25, propulsion="sail"),
    ])
    agg = aggregate_stats([v1, v2])
    assert agg["sail_nm"] == round(10.25 + 3.0, 1)
    assert agg["motor_nm"] == 5.2  # 5.25 rounded after summation
    assert agg["total_nm"] == round(10.25 + 8.25, 1)
    assert agg["sail_hhmm"] == "2:00"       # 90 + 30 sail minutes
    assert agg["motor_hhmm"] == "1:00"
    assert agg["duration_hhmm"] == "3:00"   # sum of per-voyage wall clocks
    assert agg["voyage_count"] == 2


def test_aggregate_stats_rounds_after_summing():
    # two voyages of 0.25 Nm: rounding each first would give 0.2+0.2=0.4
    stats = compute_stats([
        _entry(minutes_offset=0,  log_value=0.0,  propulsion="motor"),
        _entry(minutes_offset=30, log_value=0.25, propulsion="motor"),
    ])
    agg = aggregate_stats([stats, stats])
    assert agg["total_nm"] == 0.5


def test_aggregate_stats_empty():
    agg = aggregate_stats([])
    assert agg["total_nm"] == 0.0
    assert agg["duration_hhmm"] == "0:00"
    assert agg["voyage_count"] == 0


def test_aggregate_stats_includes_empty_voyage():
    stats = compute_stats([
        _entry(minutes_offset=0,  log_value=0.0, propulsion="sail"),
        _entry(minutes_offset=60, log_value=6.0, propulsion="sail"),
    ])
    agg = aggregate_stats([stats, compute_stats([])])
    assert agg["total_nm"] == 6.0
    assert agg["voyage_count"] == 2
