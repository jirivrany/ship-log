"""Per-location statistics and destination comparison (PRD §8, §9, §10).

summarize() returns exactly the keys the JSON export specifies, so the export
is a serialization of this result rather than a second implementation.
"""
from dataclasses import dataclass
from statistics import median
from typing import Iterable, Optional

from app.analysis.aggregate import (
    OCTANTS,
    WIND_BINS,
    DailyAggregate,
    Thresholds,
    wind_bin,
)
from app.models import SailingRating

# Wind-day categories (PRD §8). The PRD names them but leaves the limits to
# the implementation, so they are derived from the classification thresholds
# instead of introducing unrelated numbers: "good sailing" starts where the
# distribution bins do, "strong" at the yellow limit, "dangerous" at the red.
LIGHT_MAX_KN = 10.0


def _pct(count: int, total: int) -> float:
    return round(100.0 * count / total, 1) if total else 0.0


def _counts_with_pct(counts: dict, total: int, keys: Iterable) -> dict:
    return {
        str(key): {"days": counts.get(key, 0),
                   "percent": _pct(counts.get(key, 0), total)}
        for key in keys
    }


@dataclass(frozen=True)
class LocationSummary:
    """Everything the comparison table and charts need for one location."""
    location_key: str
    name: str
    region: str
    days: int
    windows: int
    green_pct: float
    yellow_pct: float
    red_pct: float
    lost_day_probability: float
    mean_lost_days: float
    median_max_wind_kn: Optional[float]
    median_rain_mm: Optional[float]
    statistics: dict           # the §10 JSON body for this location


def wind_categories(days: list[DailyAggregate], thresholds: Thresholds) -> dict:
    """Light / good sailing / strong / dangerous day counts (PRD §8)."""
    rated = [d for d in days if d.max_wind_kn is not None]
    total = len(rated)
    counts = {"light": 0, "good_sailing": 0, "strong": 0, "dangerous": 0}
    for day in rated:
        wind = day.max_wind_kn
        if wind > thresholds.red_wind_kn:
            counts["dangerous"] += 1
        elif wind >= thresholds.yellow_wind_kn:
            counts["strong"] += 1
        elif wind >= LIGHT_MAX_KN:
            counts["good_sailing"] += 1
        else:
            counts["light"] += 1
    return _counts_with_pct(counts, total, counts)


def rainfall_statistics(days: list[DailyAggregate],
                        thresholds: Thresholds) -> dict:
    """Rainy days, heavy-rain days, and consecutive-rain events (PRD §8)."""
    measured = [d for d in days if d.rain_total_mm is not None]
    total = len(measured)
    rainy = [d for d in measured if d.rain_total_mm >= thresholds.yellow_rain_mm]
    heavy = [d for d in measured if d.rain_total_mm > thresholds.red_rain_mm]

    # Consecutive-rain events are counted per window, so a run cannot straddle
    # two different years' windows.
    runs: list[int] = []
    for _, window_days in _by_window(measured):
        current = 0
        for day in window_days:
            if day.rain_total_mm >= thresholds.yellow_rain_mm:
                current += 1
            else:
                if current > 1:
                    runs.append(current)
                current = 0
        if current > 1:
            runs.append(current)

    rain_values = [d.rain_total_mm for d in measured]
    return {
        "rainy_days": len(rainy),
        "rainy_day_percent": _pct(len(rainy), total),
        "heavy_rain_days": len(heavy),
        "heavy_rain_day_percent": _pct(len(heavy), total),
        "consecutive_rain_events": len(runs),
        "longest_consecutive_rain_days": max(runs) if runs else 0,
        "median_daily_rain_mm": round(median(rain_values), 2) if rain_values else None,
        "max_daily_rain_mm": round(max(rain_values), 2) if rain_values else None,
    }


def _by_window(days: list[DailyAggregate]) -> list[tuple[int, list[DailyAggregate]]]:
    """Days grouped by their window year, each in calendar order."""
    grouped: dict[int, list[DailyAggregate]] = {}
    for day in days:
        grouped.setdefault(day.window_year, []).append(day)
    return [(year, sorted(grouped[year], key=lambda d: d.local_date))
            for year in sorted(grouped)]


def lost_day_statistics(days: list[DailyAggregate]) -> dict:
    """Lost days per window, the mean, and P(window has at least one)."""
    windows = _by_window(days)
    per_window = {str(year): sum(1 for d in window if d.lost_day)
                  for year, window in windows}
    counts = list(per_window.values())
    with_any = sum(1 for c in counts if c > 0)
    return {
        "per_window": per_window,
        "total_lost_days": sum(counts),
        "mean_lost_days_per_window": round(sum(counts) / len(counts), 2) if counts else 0.0,
        "windows_analyzed": len(counts),
        "windows_with_lost_day": with_any,
        "probability_window_has_lost_day": _pct(with_any, len(counts)),
    }


def summarize(days: list[DailyAggregate], thresholds: Thresholds) -> dict:
    """The §10 summary body for one location."""
    rated = [d for d in days if d.sailing_rating is not None]
    total = len(rated)

    rating_counts = {r: sum(1 for d in rated if d.sailing_rating == r)
                     for r in SailingRating}
    bin_counts: dict[str, int] = {}
    for day in days:
        if day.max_wind_kn is not None:
            label = wind_bin(day.max_wind_kn)
            bin_counts[label] = bin_counts.get(label, 0) + 1
    direction_counts: dict[str, int] = {}
    for day in days:
        if day.dominant_direction:
            direction_counts[day.dominant_direction] = (
                direction_counts.get(day.dominant_direction, 0) + 1)

    return {
        "days_analyzed": len(days),
        "wind_bins": _counts_with_pct(
            bin_counts, sum(bin_counts.values()), [b[1] for b in WIND_BINS]),
        "direction_distribution": _counts_with_pct(
            direction_counts, sum(direction_counts.values()), OCTANTS),
        "rainfall_statistics": rainfall_statistics(days, thresholds),
        "sailing_categories": {
            "ratings": _counts_with_pct(
                {r.value: rating_counts[r] for r in SailingRating},
                total, [r.value for r in SailingRating]),
            "wind_days": wind_categories(days, thresholds),
        },
        "lost_day_statistics": lost_day_statistics(days),
    }


def summarize_location(
    location_key: str,
    name: str,
    region: str,
    days: list[DailyAggregate],
    thresholds: Thresholds,
) -> LocationSummary:
    stats = summarize(days, thresholds)
    ratings = stats["sailing_categories"]["ratings"]
    winds = [d.max_wind_kn for d in days if d.max_wind_kn is not None]
    rains = [d.rain_total_mm for d in days if d.rain_total_mm is not None]

    return LocationSummary(
        location_key=location_key,
        name=name,
        region=region,
        days=len(days),
        windows=stats["lost_day_statistics"]["windows_analyzed"],
        green_pct=ratings["green"]["percent"],
        yellow_pct=ratings["yellow"]["percent"],
        red_pct=ratings["red"]["percent"],
        lost_day_probability=stats["lost_day_statistics"]["probability_window_has_lost_day"],
        mean_lost_days=stats["lost_day_statistics"]["mean_lost_days_per_window"],
        median_max_wind_kn=round(median(winds), 1) if winds else None,
        median_rain_mm=round(median(rains), 2) if rains else None,
        statistics=stats,
    )


def rank(summaries: list[LocationSummary]) -> list[LocationSummary]:
    """Most to least suitable: green days first, lost-day risk breaks ties."""
    return sorted(summaries,
                  key=lambda s: (-s.green_pct, s.lost_day_probability, s.name))


def comparison_statement(summaries: list[LocationSummary]) -> Optional[str]:
    """A qualified suitability sentence (PRD §9) — never a forecast."""
    usable = [s for s in summaries if s.days]
    if not usable:
        return None
    ordered = rank(usable)
    best = ordered[0]
    windows = best.windows

    if len(ordered) == 1:
        return (
            f"Across {windows} historical window{'s' if windows != 1 else ''}, "
            f"{best.name} had {best.green_pct}% green days and a "
            f"{best.lost_day_probability}% chance of at least one lost day. "
            f"These are historical frequencies, not a forecast."
        )

    worst = ordered[-1]
    return (
        f"Across {windows} historical window{'s' if windows != 1 else ''}, "
        f"{best.name} had the highest share of green days ({best.green_pct}%) "
        f"and {worst.name} the lowest ({worst.green_pct}%). "
        f"Lost-day probability ranged from {min(s.lost_day_probability for s in ordered)}% "
        f"to {max(s.lost_day_probability for s in ordered)}%. "
        f"These are historical frequencies, not a forecast."
    )
