from datetime import datetime, timezone
from typing import Optional

from app.models import EntrySource, LogEntry


def _format_hhmm(minutes: float) -> str:
    total = int(minutes)
    return f"{total // 60}:{total % 60:02d}"


def compute_stats(entries: list, *, wall_clock_duration: bool = True) -> dict:
    """Compute distance and time stats from a list of LogEntry objects.

    Iterates consecutive entry pairs and accumulates nautical miles and minutes
    per propulsion bucket.  Entries with propulsion=None go into "unknown".
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
        key = prev.propulsion.value if prev.propulsion else "unknown"
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
        "unknown_nm": round(nm_by_prop.get("unknown", 0.0), 1),
        "duration_hhmm": _format_hhmm(duration_minutes),
        "motor_hhmm":    _format_hhmm(min_by_prop.get("motor",   0.0)),
        "sail_hhmm":     _format_hhmm(min_by_prop.get("sail",    0.0)),
        "both_hhmm":     _format_hhmm(min_by_prop.get("both",    0.0)),
        "entry_count": len(entries),
        "lap_count":   lap_count,
    }
