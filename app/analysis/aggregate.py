"""Daily aggregation and sailing-condition classification (PRD §8)."""
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from typing import Iterable, Optional

from app.analysis.provider import HourlyObservation
from app.models import SailingRating

# Eight-point sectors (the log's own weather module uses sixteen; the
# analyzer's distributions are specified at eight).
OCTANTS = ("N", "NE", "E", "SE", "S", "SW", "W", "NW")

# Wind-speed distribution bins as lower bound (inclusive) and label.
WIND_BINS = (
    (0.0, "<10"),
    (10.0, "10-15"),
    (15.0, "15-20"),
    (20.0, "20-25"),
    (25.0, "25-30"),
    (30.0, ">30"),
)


@dataclass(frozen=True)
class Thresholds:
    """Classification limits, frozen onto each run so results stay reproducible.

    Boundaries follow PRD §8 exactly: yellow limits are inclusive (20 kn and
    5 mm are yellow; 30 kn and 30 mm stay yellow) and red needs a value
    strictly greater than its limit.
    """
    yellow_wind_kn: float = 20.0
    yellow_rain_mm: float = 5.0
    red_wind_kn: float = 30.0
    red_gust_kn: float = 40.0
    red_rain_mm: float = 30.0
    # The lost-day rain limit is deliberately lower than the red one, so a
    # yellow day can still cost a day's sailing.
    lost_wind_kn: float = 30.0
    lost_rain_mm: float = 20.0

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Thresholds":
        if not data:
            return cls()
        known = {f: data[f] for f in cls.__dataclass_fields__ if f in data}
        return cls(**{k: float(v) for k, v in known.items()})


@dataclass(frozen=True)
class DailyAggregate:
    """One local calendar day at one location."""
    local_date: date
    window_year: int
    avg_wind_kn: Optional[float]
    max_wind_kn: Optional[float]
    max_gust_kn: Optional[float]
    rain_total_mm: Optional[float]
    dominant_direction: Optional[str]
    sailing_rating: Optional[SailingRating]
    lost_day: bool
    hours: int                      # valid hourly samples behind the averages


def degrees_to_octant(degrees: float) -> str:
    """Eight-point compass sector; each spans 45 deg centred on its heading."""
    return OCTANTS[int((degrees % 360) / 45 + 0.5) % 8]


def wind_bin(speed_kn: float) -> str:
    """Label of the distribution bin a wind speed falls in (PRD §8)."""
    label = WIND_BINS[0][1]
    for lower, name in WIND_BINS:
        if speed_kn >= lower:
            label = name
    return label


def rate_day(
    max_wind_kn: Optional[float],
    max_gust_kn: Optional[float],
    rain_total_mm: Optional[float],
    thresholds: Thresholds,
) -> Optional[SailingRating]:
    """Green/yellow/red for one day; None when nothing was measured.

    Red takes precedence over yellow, yellow over green.
    """
    if max_wind_kn is None and rain_total_mm is None:
        return None
    wind = max_wind_kn or 0.0
    gust = max_gust_kn or 0.0
    rain = rain_total_mm or 0.0

    if (wind > thresholds.red_wind_kn
            or gust > thresholds.red_gust_kn
            or rain > thresholds.red_rain_mm):
        return SailingRating.red
    if wind >= thresholds.yellow_wind_kn or rain >= thresholds.yellow_rain_mm:
        return SailingRating.yellow
    return SailingRating.green


def is_lost_day(
    max_wind_kn: Optional[float],
    rain_total_mm: Optional[float],
    thresholds: Thresholds,
) -> bool:
    """PRD §8 default: max_wind_kn > 30 or rain_total_mm > 20."""
    return ((max_wind_kn or 0.0) > thresholds.lost_wind_kn
            or (rain_total_mm or 0.0) > thresholds.lost_rain_mm)


def aggregate_days(
    observations: Iterable[HourlyObservation],
    window_year: int,
    thresholds: Optional[Thresholds] = None,
) -> list[DailyAggregate]:
    """Group hourly observations into classified local calendar days.

    Grouping is by the location's local date, so a day across a DST transition
    correctly holds 23 or 25 hours — including both halves of a repeated hour.
    """
    thresholds = thresholds or Thresholds()

    by_day: dict[date, list[HourlyObservation]] = {}
    for observation in observations:
        by_day.setdefault(observation.local_date, []).append(observation)

    days = []
    for local_date in sorted(by_day):
        hours = by_day[local_date]
        winds = [h.wind_speed_kn for h in hours if h.wind_speed_kn is not None]
        gusts = [h.wind_gust_kn for h in hours if h.wind_gust_kn is not None]
        rain = [h.precipitation_mm for h in hours
                if h.precipitation_mm is not None]
        sectors = [degrees_to_octant(h.wind_direction_deg) for h in hours
                   if h.wind_direction_deg is not None]

        avg_wind = round(sum(winds) / len(winds), 2) if winds else None
        max_wind = max(winds) if winds else None
        max_gust = max(gusts) if gusts else None
        rain_total = round(sum(rain), 2) if rain else None
        # Ties go to the sector nearest north, so the result is deterministic
        # rather than dependent on dict ordering.
        dominant = None
        if sectors:
            counts = Counter(sectors)
            best = max(counts.values())
            dominant = next(o for o in OCTANTS if counts.get(o) == best)

        days.append(DailyAggregate(
            local_date=local_date,
            window_year=window_year,
            avg_wind_kn=avg_wind,
            max_wind_kn=max_wind,
            max_gust_kn=max_gust,
            rain_total_mm=rain_total,
            dominant_direction=dominant,
            sailing_rating=rate_day(max_wind, max_gust, rain_total, thresholds),
            lost_day=is_lost_day(max_wind, rain_total, thresholds),
            hours=len(winds),
        ))
    return days
