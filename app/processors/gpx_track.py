"""GPX adapter: reads track points into RawPoints for process_track().

All <trk>/<trkseg> elements are concatenated — one GPX file is always one
leg. GPX carries no speed field, so process_track() derives it from
position deltas; air temperature comes from Garmin's TrackPointExtension
(ns3:atemp) when present.
"""
import re
from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import gpxpy

from app.processors.track import (
    KNOTS_PER_MS,
    ProcessedTrack,
    RawPoint,
    TrackMeta,
    process_track,
)
from app.processors.tz import tz_name_at


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _atemp(point) -> Optional[float]:
    """Air temperature from Garmin's TrackPointExtension, if present."""
    for ext in point.extensions:
        for el in ext.iter():
            if el.tag.endswith("atemp"):
                try:
                    return float(el.text)
                except (TypeError, ValueError):
                    return None
    return None


def _read_points(gpx) -> list[RawPoint]:
    points: list[RawPoint] = []
    for trk in gpx.tracks:
        for seg in trk.segments:
            for pt in seg.points:
                if pt.time is None:
                    continue
                # GPX 1.0 files may carry a speed element (m/s); 1.1 doesn't
                speed = getattr(pt, "speed", None)
                points.append(RawPoint(
                    timestamp=_ensure_utc(pt.time),
                    lat=pt.latitude,
                    lon=pt.longitude,
                    speed_knots=speed * KNOTS_PER_MS if speed is not None else None,
                    temp=_atemp(pt),
                ))
    return points


def parse_gpx_track(path: str) -> ProcessedTrack:
    with open(path, encoding="utf-8") as f:
        gpx = gpxpy.parse(f)
    return process_track(_read_points(gpx))


def parse_gpx_metadata(path: str, filename: str) -> TrackMeta:
    """Extract date, ports, total distance and timezone from GPX file + filename."""
    with open(path, encoding="utf-8") as f:
        gpx = gpxpy.parse(f)

    points = _read_points(gpx)
    start_time = points[0].timestamp if points else None
    tz_name = tz_name_at(points[0].lat, points[0].lon) if points else "UTC"

    date = ""
    if start_time:
        date = start_time.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")

    dist_m = gpx.length_2d()
    total_distance_nm = round(dist_m / 1852.0, 2) if dist_m else None

    track_name = gpx.tracks[0].name if gpx.tracks else None
    from_port, to_port = _ports_from_name(track_name)
    if not (from_port and to_port):
        from_port, to_port = _ports_from_filename(filename)

    return TrackMeta(
        date=date,
        from_port=from_port,
        to_port=to_port,
        total_distance_nm=total_distance_nm,
        start_time=start_time,
        timezone=tz_name,
    )


def _ports_from_name(name: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Split a 'From - To' track name; Garmin auto-titles won't match."""
    if not name:
        return None, None
    parts = re.split(r"\s+[-–]\s+", name.strip())
    if len(parts) >= 2 and parts[0].strip() and parts[-1].strip():
        return parts[0].strip(), parts[-1].strip()
    return None, None


def _ports_from_filename(filename: str) -> tuple[Optional[str], Optional[str]]:
    """
    Parse ports from a filename like 0620_1_Sukosan-Zdrelac.gpx:
    strip the extension and any leading date/leg-number digits, then
    split the rest on '-'. Underscores read as spaces (Sv_Ante → Sv Ante).
    """
    stem = re.sub(r"\.gpx$", "", filename, flags=re.IGNORECASE)
    stem = re.sub(r"^[\d_]+", "", stem)
    stem = re.sub(r"[^\w\-]", "_", stem)
    stem = stem.replace("_", " ").strip()

    parts = [p.strip() for p in stem.split("-")]
    if len(parts) >= 2 and parts[0] and parts[-1]:
        return parts[0], parts[-1]
    return None, None
