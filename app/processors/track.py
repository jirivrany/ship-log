"""Source-neutral track processing.

Adapters (FIT, GPX, Strava streams) each produce a list of RawPoint plus
optional LapPoints; process_track() owns everything downstream: sampling,
course computation, cumulative distance, turning-point detection and
hourly marks. The sailing math engine therefore behaves identically
regardless of where the telemetry came from.
"""
import math
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

SAMPLE_INTERVAL_S = 30

# Regime-change detection parameters
TP_WINDOW = 5          # samples each side for stable-regime median (5 × 30s = 2.5 min)
TP_MIN_CHANGE = 25.0   # degrees: minimum sustained course change to qualify
TP_MIN_DIST_M = 300    # metres: minimum distance between two accepted turning points

KNOTS_PER_MS = 1.94384


@dataclass
class RawPoint:
    """One telemetry sample as delivered by a source adapter."""
    timestamp: datetime              # tz-aware UTC
    lat: float
    lon: float
    speed_knots: Optional[float] = None   # None → derived from position deltas
    temp: Optional[float] = None          # air temperature °C
    dist_m: Optional[float] = None        # cumulative distance from the source, metres


@dataclass
class LapPoint:
    """A manual lap mark (Garmin lap button), whatever the source."""
    timestamp: datetime
    lat: float
    lon: float


@dataclass
class TrackMeta:
    """Prefill metadata extracted from a track file + its filename."""
    date: str                    # YYYY-MM-DD (local calendar date)
    from_port: Optional[str]
    to_port: Optional[str]
    total_distance_nm: Optional[float]
    start_time: Optional[datetime]
    timezone: str                # IANA name e.g. "Europe/Zagreb"


@dataclass
class TrackPoint:
    timestamp: datetime
    lat: float
    lon: float
    speed_knots: float
    course: Optional[float]
    air_temperature: Optional[float]
    distance_nm: float               # cumulative haversine from leg start
    raw_distance_nm: Optional[float] = None  # cumulative from the source's distance field


@dataclass
class ProcessedTrack:
    track_points: list[TrackPoint]
    turning_points: list[TrackPoint]
    hourly_points: list[TrackPoint]


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


def process_track(points: list[RawPoint]) -> ProcessedTrack:
    if not points:
        return ProcessedTrack([], [], [])

    raw = sorted(points, key=lambda p: p.timestamp)

    # Sample every 30 seconds
    sampled = [raw[0]]
    for pt in raw[1:]:
        if (pt.timestamp - sampled[-1].timestamp).total_seconds() >= SAMPLE_INTERVAL_S:
            sampled.append(pt)

    # Sources without a speed field (GPX) get speed from position deltas
    # between consecutive sampled points.
    speeds: list[Optional[float]] = []
    for i, pt in enumerate(sampled):
        if pt.speed_knots is not None:
            speeds.append(pt.speed_knots)
            continue
        if i == 0:
            speeds.append(None)  # backfilled from the next point below
            continue
        prev = sampled[i - 1]
        dt_s = (pt.timestamp - prev.timestamp).total_seconds()
        dist_m = _haversine_m(prev.lat, prev.lon, pt.lat, pt.lon)
        speeds.append(dist_m / dt_s * KNOTS_PER_MS if dt_s > 0 else 0.0)
    if speeds and speeds[0] is None:
        speeds[0] = speeds[1] if len(speeds) > 1 else 0.0

    # Build TrackPoints with bearing and cumulative distance
    track_points: list[TrackPoint] = []
    cumulative_nm = 0.0

    for i, pt in enumerate(sampled):
        if i > 0:
            prev = sampled[i - 1]
            cumulative_nm += _haversine_m(prev.lat, prev.lon, pt.lat, pt.lon) / 1852.0

        if i + 1 < len(sampled):
            nxt = sampled[i + 1]
            course = _bearing(pt.lat, pt.lon, nxt.lat, nxt.lon)
        else:
            course = track_points[-1].course if track_points else None

        track_points.append(TrackPoint(
            timestamp=pt.timestamp,
            lat=pt.lat,
            lon=pt.lon,
            speed_knots=round(speeds[i], 2),
            course=round(course, 1) if course is not None else None,
            air_temperature=pt.temp,
            distance_nm=round(cumulative_nm, 3),
            raw_distance_nm=round(pt.dist_m / 1852.0, 3) if pt.dist_m is not None else None,
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
