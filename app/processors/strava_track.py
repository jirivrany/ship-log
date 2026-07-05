"""Strava adapter: parses a persisted activity bundle (raw API responses,
saved as the leg's track file) into RawPoints for process_track().

Bundle shape (written by strava_api.fetch_activity_bundle):
  {"activity": {...}, "streams": {key_by_type dicts}, "laps": [...]}

Stream `time` values are second offsets from the activity's start_date;
`velocity_smooth` is m/s; `distance` is cumulative metres.
"""
import json
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from app.processors.gpx_track import ports_from_name
from app.processors.track import (
    KNOTS_PER_MS,
    LapPoint,
    ProcessedTrack,
    RawPoint,
    TrackMeta,
    process_track,
)
from app.processors.tz import tz_name_at


def _load_bundle(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _start_time(activity: dict) -> datetime:
    return datetime.fromisoformat(
        activity["start_date"].replace("Z", "+00:00")
    ).astimezone(timezone.utc)


def _stream(streams: dict, key: str) -> Optional[list]:
    entry = streams.get(key)
    return entry.get("data") if entry else None


def _read_points(bundle: dict) -> list[RawPoint]:
    start = _start_time(bundle["activity"])
    streams = bundle.get("streams", {})

    times = _stream(streams, "time")
    latlng = _stream(streams, "latlng")
    if not times or not latlng:
        return []
    velocity = _stream(streams, "velocity_smooth")
    distance = _stream(streams, "distance")
    temp = _stream(streams, "temp")

    def at(seq, i):
        return seq[i] if seq is not None and i < len(seq) and seq[i] is not None else None

    points: list[RawPoint] = []
    for i, offset_s in enumerate(times):
        pos = at(latlng, i)
        if not pos:  # entries are null while the device has no GPS fix
            continue
        speed_ms = at(velocity, i)
        points.append(RawPoint(
            timestamp=start + timedelta(seconds=offset_s),
            lat=pos[0],
            lon=pos[1],
            speed_knots=speed_ms * KNOTS_PER_MS if speed_ms is not None else None,
            temp=at(temp, i),
            dist_m=at(distance, i),
        ))
    return points


def parse_strava_track(path: str) -> ProcessedTrack:
    return process_track(_read_points(_load_bundle(path)))


def parse_strava_metadata(path: str, filename: str) -> TrackMeta:
    bundle = _load_bundle(path)
    activity = bundle["activity"]

    points = _read_points(bundle)
    start_time = points[0].timestamp if points else _start_time(activity)
    tz_name = tz_name_at(points[0].lat, points[0].lon) if points else "UTC"
    date = start_time.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")

    dist_m = activity.get("distance")
    from_port, to_port = ports_from_name(activity.get("name"))

    return TrackMeta(
        date=date,
        from_port=from_port,
        to_port=to_port,
        total_distance_nm=round(dist_m / 1852.0, 2) if dist_m else None,
        start_time=start_time,
        timezone=tz_name,
    )


def parse_strava_laps(path: str) -> list[LapPoint]:
    """Map device laps to lap marks, mirroring the FIT manual-lap semantics.

    Strava's lap objects don't expose a trigger type, but a device always
    closes one final lap when the activity is stopped — so the last lap is
    dropped, and each remaining lap's END (the moment the button was
    pressed) is resolved to a position via its end_index into the streams.
    A single-lap activity therefore yields no lap marks, same as a FIT
    file without manual laps.
    """
    bundle = _load_bundle(path)
    laps = bundle.get("laps") or []
    if len(laps) < 2:
        return []

    streams = bundle.get("streams", {})
    times = _stream(streams, "time")
    latlng = _stream(streams, "latlng")
    if not times or not latlng:
        return []
    start = _start_time(bundle["activity"])

    marks: list[LapPoint] = []
    for lap in sorted(laps, key=lambda l: l.get("lap_index", 0))[:-1]:
        end_idx = lap.get("end_index")
        if end_idx is None or end_idx >= len(times):
            continue
        pos = latlng[end_idx] if end_idx < len(latlng) else None
        if not pos:
            continue
        marks.append(LapPoint(
            timestamp=start + timedelta(seconds=times[end_idx]),
            lat=pos[0],
            lon=pos[1],
        ))
    return marks
