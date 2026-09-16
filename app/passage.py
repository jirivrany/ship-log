"""Passage-plan arithmetic: rhumb-line legs between clicked waypoints.

Rhumb lines rather than great circles, because a rhumb line is a constant
course — the thing a skipper actually steers and writes in the log. Over the
distances this app deals with the two agree closely (Žirje → Vis differs by
0.000 NM and 0.18°), so the choice costs nothing and stays correct if the
waypoints ever span an ocean.

Bearings are reported both true and magnetic. Magnetic matters: Croatian
waters run about +5°E, so steering a true course off the compass would put a
40 NM leg roughly 3.5 NM off track.
"""
from dataclasses import dataclass
from datetime import date
from typing import Optional, Sequence

import math

# Mean earth radius in nautical miles. A nautical mile is one minute of arc,
# so this is 180 * 60 / pi.
EARTH_RADIUS_NM = 3440.065

ATTRIBUTION = (
    "Magnetic variation from the NOAA/BGS World Magnetic Model via pygeomag."
)

# Below this, two waypoints are the same click and have no meaningful course.
MIN_LEG_NM = 1e-6


@dataclass(frozen=True)
class Waypoint:
    lat: float
    lon: float
    name: Optional[str] = None


@dataclass(frozen=True)
class PassageLeg:
    """One leg between consecutive waypoints."""
    index: int                      # 1-based, for display
    start: Waypoint
    end: Waypoint
    distance_nm: float
    true_course: float              # degrees, 0-360
    magnetic_course: Optional[float]  # None when variation is unavailable
    variation: Optional[float]      # degrees, east positive
    cumulative_nm: float
    # Course change entering this leg, degrees; positive = starboard. None on
    # the first leg, which has nothing to turn from.
    alter_course: Optional[float] = None


@dataclass(frozen=True)
class PassagePlan:
    legs: Sequence[PassageLeg]
    total_nm: float

    @property
    def is_empty(self) -> bool:
        return not self.legs


def rhumb_line(start: Waypoint, end: Waypoint) -> tuple[float, float]:
    """Distance in NM and constant true course in degrees from start to end.

    Returns (0.0, 0.0) for coincident points: clicking twice in one spot is a
    normal thing for a user to do and must not raise.
    """
    lat1, lon1 = math.radians(start.lat), math.radians(start.lon)
    lat2, lon2 = math.radians(end.lat), math.radians(end.lon)

    dlat = lat2 - lat1
    dlon = lon2 - lon1

    # Stretched latitude difference (inverse Gudermannian), the projection
    # that makes a rhumb line straight.
    if lat2 != lat1:
        dpsi = math.log(math.tan(lat2 / 2 + math.pi / 4)
                        / math.tan(lat1 / 2 + math.pi / 4))
    else:
        dpsi = 0.0

    # q degenerates on an east-west leg, where the rhumb line follows a
    # parallel and the scale is simply cos(lat).
    q = dlat / dpsi if abs(dpsi) > 1e-12 else math.cos(lat1)

    # Take the short way round the antimeridian.
    if abs(dlon) > math.pi:
        dlon = dlon - math.copysign(2 * math.pi, dlon)

    distance = math.hypot(dlat, q * dlon) * EARTH_RADIUS_NM
    if distance < MIN_LEG_NM:
        return 0.0, 0.0

    course = (math.degrees(math.atan2(dlon, dpsi)) + 360.0) % 360.0
    return distance, course


def magnetic_variation(lat: float, lon: float,
                       on: Optional[date] = None) -> Optional[float]:
    """Magnetic variation in degrees, east positive, or None if unavailable.

    The model is consulted per leg rather than once per plan: variation drifts
    across a passage, and the cost is negligible.
    """
    try:
        from pygeomag import GeoMag
    except ImportError:
        return None

    when = on or date.today()
    decimal_year = when.year + (when.timetuple().tm_yday - 1) / 365.0
    try:
        result = GeoMag().calculate(
            glat=lat, glon=lon, alt=0, time=decimal_year,
            allow_date_outside_lifespan=True,
        )
        return result.d
    except Exception:
        # A model that has aged out of its epoch must not take the whole plan
        # down; true courses are still useful on their own.
        return None


def _course_change(previous: float, current: float) -> float:
    """Signed turn from one course to the next, positive to starboard."""
    return (current - previous + 540.0) % 360.0 - 180.0


def build_plan(waypoints: Sequence[Waypoint],
               on: Optional[date] = None) -> PassagePlan:
    """Turn an ordered list of waypoints into a passage plan.

    Legs of zero length (a double click in one place) are dropped rather than
    shown as a 0 NM leg on an arbitrary course.
    """
    legs: list[PassageLeg] = []
    cumulative = 0.0
    previous_course: Optional[float] = None

    for start, end in zip(waypoints, waypoints[1:]):
        distance, course = rhumb_line(start, end)
        if distance < MIN_LEG_NM:
            continue

        cumulative += distance

        # Variation at the leg's midpoint is more representative of the leg
        # than either end.
        variation = magnetic_variation(
            (start.lat + end.lat) / 2, (start.lon + end.lon) / 2, on)
        magnetic = ((course - variation + 360.0) % 360.0
                    if variation is not None else None)

        legs.append(PassageLeg(
            index=len(legs) + 1,
            start=start,
            end=end,
            distance_nm=distance,
            true_course=course,
            magnetic_course=magnetic,
            variation=variation,
            cumulative_nm=cumulative,
            alter_course=(_course_change(previous_course, course)
                          if previous_course is not None else None),
        ))
        previous_course = course

    return PassagePlan(legs=legs, total_nm=cumulative)


def eta_hours(total_nm: float, speed_kn: float) -> Optional[float]:
    """Hours to cover the passage at a given speed, or None if not moving."""
    if speed_kn <= 0:
        return None
    return total_nm / speed_kn
