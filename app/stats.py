from datetime import datetime, timezone
from typing import Optional

from app.models import EntrySource, LogEntry


def _format_hhmm(minutes: float) -> str:
    total = int(minutes)
    return f"{total // 60}:{total % 60:02d}"


def compute_stats(entries: list, *, wall_clock_duration: bool = True) -> dict:
    """Compute distance and time stats from a list of LogEntry objects.

    Iterates consecutive entry pairs and accumulates nautical miles and minutes
    per propulsion bucket. Propulsion is always set (defaults to motor), so
    every segment is attributed to a concrete bucket.
    Segments with dist <= 0 are skipped (handles cross-leg log_value resets).

    wall_clock_duration=True (default): total duration = last.timestamp - first.timestamp.
    wall_clock_duration=False: total duration = sum of per-propulsion segment times.
    """
    nm_by_prop: dict[str, float] = {}
    min_by_prop: dict[str, float] = {}

    for i in range(1, len(entries)):
        prev, cur = entries[i - 1], entries[i]
        if prev.log_value is None or cur.log_value is None:
            continue
        dist = cur.log_value - prev.log_value
        if dist <= 0:
            continue
        key = prev.propulsion.value
        nm_by_prop[key] = nm_by_prop.get(key, 0.0) + dist
        if prev.timestamp and cur.timestamp:
            mins = (cur.timestamp - prev.timestamp).total_seconds() / 60
            min_by_prop[key] = min_by_prop.get(key, 0.0) + mins

    total = sum(nm_by_prop.values())

    if wall_clock_duration and len(entries) >= 2 and entries[0].timestamp and entries[-1].timestamp:
        duration_minutes = (entries[-1].timestamp - entries[0].timestamp).total_seconds() / 60
    else:
        duration_minutes = sum(min_by_prop.values())

    lap_count = sum(1 for e in entries if e.source == EntrySource.lap)

    return {
        "total_nm":   round(total, 1),
        "motor_nm":   round(nm_by_prop.get("motor",   0.0), 1),
        "sail_nm":    round(nm_by_prop.get("sail",    0.0), 1),
        "both_nm":    round(nm_by_prop.get("both",    0.0), 1),
        "anchor_nm":  round(nm_by_prop.get("anchor",  0.0), 1),
        "duration_hhmm": _format_hhmm(duration_minutes),
        "motor_hhmm":    _format_hhmm(min_by_prop.get("motor",   0.0)),
        "sail_hhmm":     _format_hhmm(min_by_prop.get("sail",    0.0)),
        "both_hhmm":     _format_hhmm(min_by_prop.get("both",    0.0)),
        "entry_count": len(entries),
        "lap_count":   lap_count,
        # unrounded values for cross-voyage aggregation (see aggregate_stats)
        "raw": {
            "total_nm":     total,
            "motor_nm":     nm_by_prop.get("motor",  0.0),
            "sail_nm":      nm_by_prop.get("sail",   0.0),
            "both_nm":      nm_by_prop.get("both",   0.0),
            "anchor_nm":    nm_by_prop.get("anchor", 0.0),
            "duration_min": duration_minutes,
            "motor_min":    min_by_prop.get("motor",  0.0),
            "sail_min":     min_by_prop.get("sail",   0.0),
            "both_min":     min_by_prop.get("both",   0.0),
            "anchor_min":   min_by_prop.get("anchor", 0.0),
        },
    }


def aggregate_stats(stats_list: list[dict]) -> dict:
    """Sum per-voyage compute_stats() results into one formatted summary.

    Sums the unrounded `raw` values (formatted H:MM strings and rounded Nm
    are not safe to add), then formats like compute_stats. Total duration is
    the sum of per-voyage wall-clock durations — wall clock across voyages
    would span the gaps between them.
    """
    sums: dict[str, float] = {}
    for stats in stats_list:
        for key, value in stats["raw"].items():
            sums[key] = sums.get(key, 0.0) + value

    return {
        "total_nm":   round(sums.get("total_nm",  0.0), 1),
        "motor_nm":   round(sums.get("motor_nm",  0.0), 1),
        "sail_nm":    round(sums.get("sail_nm",   0.0), 1),
        "both_nm":    round(sums.get("both_nm",   0.0), 1),
        "anchor_nm":  round(sums.get("anchor_nm", 0.0), 1),
        "duration_hhmm": _format_hhmm(sums.get("duration_min", 0.0)),
        "motor_hhmm":    _format_hhmm(sums.get("motor_min",    0.0)),
        "sail_hhmm":     _format_hhmm(sums.get("sail_min",     0.0)),
        "both_hhmm":     _format_hhmm(sums.get("both_min",     0.0)),
        "voyage_count": len(stats_list),
    }
