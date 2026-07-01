import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import fitparse

SEMICIRCLE_TO_DEG = 180.0 / (2**31)
SAMPLE_INTERVAL_S = 30

# Regime-change detection parameters
TP_WINDOW = 5          # samples each side for stable-regime median (5 × 30s = 2.5 min)
TP_MIN_CHANGE = 25.0   # degrees: minimum sustained course change to qualify
TP_MIN_DIST_M = 300    # metres: minimum distance between two accepted turning points


@dataclass
class TrackPoint:
    timestamp: datetime
    lat: float
    lon: float
    speed_knots: float
    course: Optional[float]
    air_temperature: Optional[float]
    distance_nm: float               # cumulative haversine from leg start
    raw_distance_nm: Optional[float] = None  # cumulative from FIT file's distance field


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


def _circular_median(angles: list[float]) -> float:
    """Circular median: return the angle that minimises total arc distance to all others."""
    best, best_cost = 0.0, float('inf')
    for candidate in angles:
        cost = sum(_bearing_delta(candidate, a) for a in angles)
        if cost < best_cost:
            best_cost = cost
            best = candidate
    return best


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def parse_fit_track(path: str) -> ProcessedTrack:
    fit = fitparse.FitFile(path)

    # Collect all records with position
    raw: list[tuple[datetime, float, float, float, Optional[float], Optional[float]]] = []
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
        speed_ms = fields.get("enhanced_speed") or fields.get("speed") or 0.0
        speed_kn = speed_ms * 1.94384
        temp = fields.get("temperature")
        dist_m = fields.get("distance")
        raw_dist_nm = round(dist_m / 1852.0, 3) if dist_m is not None else None
        raw.append((ts, lat, lon, speed_kn, temp, raw_dist_nm))

    if not raw:
        return ProcessedTrack([], [], [])

    raw.sort(key=lambda x: x[0])

    # Sample every 30 seconds
    sampled = [raw[0]]
    for row in raw[1:]:
        if (row[0] - sampled[-1][0]).total_seconds() >= SAMPLE_INTERVAL_S:
            sampled.append(row)

    # Build TrackPoints with bearing and cumulative distance
    track_points: list[TrackPoint] = []
    cumulative_nm = 0.0

    for i, (ts, lat, lon, speed_kn, temp, raw_dist_nm) in enumerate(sampled):
        if i > 0:
            prev = sampled[i - 1]
            cumulative_nm += _haversine_m(prev[1], prev[2], lat, lon) / 1852.0

        if i + 1 < len(sampled):
            _, nlat, nlon, _, _, _ = sampled[i + 1]
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
            raw_distance_nm=raw_dist_nm,
        ))

    # ------------------------------------------------------------------ #
    # Turning point detection — two passes                                 #
    #                                                                      #
    # Pass 1 — regime detection                                            #
    #   For each point i compare the circular median of the W samples      #
    #   before vs W samples after.  Median is robust to GPS scatter at     #
    #   low speed.  Score = change × tanh(avg_window_speed) suppresses     #
    #   dock wobble.  Cooldown from last accepted point (not cluster        #
    #   anchor) so two real turns close together can both survive.         #
    #                                                                      #
    # Pass 2 — apex refinement                                             #
    #   Each regime-change point is only a rough location (centre of the   #
    #   detection window).  Within ±W samples around it find the single    #
    #   point with the sharpest instantaneous bearing change                #
    #   (course[i-1] → course[i+1]) weighted by speed — that is the        #
    #   actual moment the helm went over.                                   #
    # ------------------------------------------------------------------ #
    W = TP_WINDOW
    n = len(track_points)

    # Pass 1: regime detection
    regime_indices: list[int] = []   # indices of detected regime changes
    last_accepted_idx: Optional[int] = None
    last_accepted_score: float = 0.0

    for i in range(W, n - W):
        before_courses = [track_points[j].course for j in range(i - W, i)
                          if track_points[j].course is not None]
        after_courses  = [track_points[j].course for j in range(i + 1, i + W + 1)
                          if track_points[j].course is not None]
        if len(before_courses) < W or len(after_courses) < W:
            continue

        med_before = _circular_median(before_courses)
        med_after  = _circular_median(after_courses)
        change = _bearing_delta(med_before, med_after)
        if change < TP_MIN_CHANGE:
            continue

        before_speeds = [track_points[j].speed_knots for j in range(i - W, i)]
        after_speeds  = [track_points[j].speed_knots for j in range(i + 1, i + W + 1)]
        avg_speed = (sum(before_speeds) + sum(after_speeds)) / (len(before_speeds) + len(after_speeds))
        score = change * math.tanh(avg_speed)

        pt = track_points[i]
        if last_accepted_idx is not None:
            last_pt = track_points[last_accepted_idx]
            dist = _haversine_m(last_pt.lat, last_pt.lon, pt.lat, pt.lon)
            if dist < TP_MIN_DIST_M:
                if score > last_accepted_score:
                    regime_indices[-1] = i
                    last_accepted_idx = i
                    last_accepted_score = score
                continue

        regime_indices.append(i)
        last_accepted_idx = i
        last_accepted_score = score

    # Pass 2: apex refinement — within ±W of each regime-change index
    # find the point with the sharpest instantaneous turn at speed.
    def _instantaneous_score(i: int) -> float:
        if i < 1 or i >= n - 1:
            return 0.0
        c_prev = track_points[i - 1].course
        c_next = track_points[i + 1].course
        if c_prev is None or c_next is None:
            return 0.0
        return _bearing_delta(c_prev, c_next) * math.tanh(track_points[i].speed_knots)

    turning_points: list[TrackPoint] = []
    for regime_i in regime_indices:
        search_start = max(1, regime_i - W)
        search_end   = min(n - 2, regime_i + W)
        best_i = max(range(search_start, search_end + 1), key=_instantaneous_score)
        turning_points.append(track_points[best_i])

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
