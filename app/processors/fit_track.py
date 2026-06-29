import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import fitparse

SEMICIRCLE_TO_DEG = 180.0 / (2**31)
SAMPLE_INTERVAL_S = 30
TURNING_POINT_MIN_DISTANCE_M = 400   # min distance between accepted turning points
TURNING_POINT_MIN_BEARING_CHANGE = 20.0  # degrees sustained change required
TURNING_POINT_WINDOW = 3             # samples each side for before/after avg bearing


@dataclass
class TrackPoint:
    timestamp: datetime
    lat: float
    lon: float
    speed_knots: float
    course: Optional[float]
    air_temperature: Optional[float]
    distance_nm: float          # cumulative from leg start


@dataclass
class ProcessedTrack:
    track_points: list[TrackPoint]
    turning_points: list[TrackPoint]
    hourly_points: list[TrackPoint]


def _sc(value: int) -> float:
    return value * SEMICIRCLE_TO_DEG


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371000.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlambda = math.radians(lon2 - lon1)
    x = math.sin(dlambda) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlambda)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def _bearing_delta(b1: float, b2: float) -> float:
    delta = abs(b2 - b1) % 360
    return delta if delta <= 180 else 360 - delta


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def parse_fit_track(path: str) -> ProcessedTrack:
    fit = fitparse.FitFile(path)

    # Collect all records with position
    raw: list[tuple[datetime, float, float, float, Optional[float]]] = []
    for msg in fit.get_messages("record"):
        fields = {f.name: f.value for f in msg.fields if f.value is not None}
        ts = fields.get("timestamp")
        lat_sc = fields.get("position_lat")
        lon_sc = fields.get("position_long")
        if ts is None or lat_sc is None or lon_sc is None:
            continue
        ts = _ensure_utc(ts)
        lat = _sc(lat_sc)
        lon = _sc(lon_sc)
        # speed from FIT is m/s → knots
        speed_ms = fields.get("enhanced_speed") or fields.get("speed") or 0.0
        speed_kn = speed_ms * 1.94384
        temp = fields.get("temperature")
        raw.append((ts, lat, lon, speed_kn, temp))

    if not raw:
        return ProcessedTrack([], [], [])

    raw.sort(key=lambda x: x[0])

    # Sample every 30 seconds
    sampled = [raw[0]]
    for row in raw[1:]:
        if (row[0] - sampled[-1][0]).total_seconds() >= SAMPLE_INTERVAL_S:
            sampled.append(row)

    # Build TrackPoints with bearing and cumulative distance in Nm
    track_points: list[TrackPoint] = []
    cumulative_nm = 0.0

    for i, (ts, lat, lon, speed_kn, temp) in enumerate(sampled):
        if i > 0:
            prev = sampled[i - 1]
            cumulative_nm += _haversine_m(prev[1], prev[2], lat, lon) / 1852.0

        if i + 1 < len(sampled):
            _, nlat, nlon, _, _ = sampled[i + 1]
            course = _bearing(lat, lon, nlat, nlon)
        else:
            course = track_points[-1].course if track_points else None

        track_points.append(TrackPoint(
            timestamp=ts,
            lat=lat,
            lon=lon,
            speed_knots=round(speed_kn, 2),
            course=round(course, 1) if course is not None else None,
            air_temperature=temp,
            distance_nm=round(cumulative_nm, 3),
        ))

    # Detect turning points using windowed before/after average bearing.
    # For each candidate we compare the circular mean of W samples before vs W after.
    # Within the cooldown distance we keep only the candidate with the largest change.
    turning_points: list[TrackPoint] = []
    W = TURNING_POINT_WINDOW
    n = len(track_points)

    # First pass: collect all candidates with their bearing change magnitude
    candidates: list[tuple[int, float]] = []  # (index, bearing_change)
    for i in range(W, n - W):
        before = [track_points[j].course for j in range(i - W, i)
                  if track_points[j].course is not None]
        after  = [track_points[j].course for j in range(i + 1, i + W + 1)
                  if track_points[j].course is not None]
        if len(before) < W or len(after) < W:
            continue
        sins_b = sum(math.sin(math.radians(b)) for b in before)
        coss_b = sum(math.cos(math.radians(b)) for b in before)
        sins_a = sum(math.sin(math.radians(a)) for a in after)
        coss_a = sum(math.cos(math.radians(a)) for a in after)
        avg_before = (math.degrees(math.atan2(sins_b, coss_b)) + 360) % 360
        avg_after  = (math.degrees(math.atan2(sins_a, coss_a)) + 360) % 360
        change = _bearing_delta(avg_before, avg_after)
        if change >= TURNING_POINT_MIN_BEARING_CHANGE:
            candidates.append((i, change))

    # Second pass: enforce cooldown — group candidates that are within
    # TURNING_POINT_MIN_DISTANCE_M of each other into clusters, emit only
    # the candidate with the largest bearing change per cluster.
    last_accepted: Optional[TrackPoint] = None
    cluster: list[tuple[int, float]] = []  # (idx, change) within current cluster

    def _emit_cluster():
        nonlocal last_accepted
        if not cluster:
            return
        best_idx, _ = max(cluster, key=lambda x: x[1])
        pt = track_points[best_idx]
        turning_points.append(pt)
        last_accepted = pt
        cluster.clear()

    for idx, change in candidates:
        pt = track_points[idx]
        if not cluster:
            cluster.append((idx, change))
        else:
            # Measure distance from the first point of the current cluster
            anchor = track_points[cluster[0][0]]
            dist = _haversine_m(anchor.lat, anchor.lon, pt.lat, pt.lon)
            if dist < TURNING_POINT_MIN_DISTANCE_M:
                cluster.append((idx, change))
            else:
                # New cluster — flush the old one first
                _emit_cluster()
                cluster.append((idx, change))

    _emit_cluster()

    # Hourly points
    hourly_points: list[TrackPoint] = []
    start_ts = track_points[0].timestamp
    next_hour_mark = 3600.0

    for pt in track_points[1:]:
        elapsed = (pt.timestamp - start_ts).total_seconds()
        if elapsed >= next_hour_mark:
            hourly_points.append(pt)
            next_hour_mark += 3600.0

    return ProcessedTrack(
        track_points=track_points,
        turning_points=turning_points,
        hourly_points=hourly_points,
    )
