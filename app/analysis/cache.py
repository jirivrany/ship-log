"""SQLite cache of normalized hourly observations (PRD §7).

ERA5 is immutable, so a cached location-day stays valid forever and a repeated
request must reuse it instead of calling the provider again (AC7).

Cached rows hold the *local* day's observations, keyed by the location, so a
timezone change on a location makes its old rows unreachable rather than
wrong — the key includes only what the stored values actually depend on.
"""
import json
from datetime import date, datetime, timezone
from typing import Iterable, Optional

from sqlmodel import Session, select

from app.analysis.provider import (
    HourlyObservation,
    ProviderResult,
    WeatherArchiveProvider,
)
from app.models import HourlyObservationCache


def _serialize(observations: Iterable[HourlyObservation]) -> str:
    return json.dumps([
        {
            "utc": o.timestamp_utc.isoformat(),
            "local": o.local_timestamp.isoformat(),
            "wind": o.wind_speed_kn,
            "dir": o.wind_direction_deg,
            "gust": o.wind_gust_kn,
            "rain": o.precipitation_mm,
            "pressure": o.pressure_msl_hpa,
        }
        for o in observations
    ])


def observations_from_row(payload: str) -> list[HourlyObservation]:
    return [
        HourlyObservation(
            timestamp_utc=datetime.fromisoformat(row["utc"]),
            local_timestamp=datetime.fromisoformat(row["local"]),
            wind_speed_kn=row["wind"],
            wind_direction_deg=row["dir"],
            wind_gust_kn=row["gust"],
            precipitation_mm=row["rain"],
            pressure_msl_hpa=row["pressure"],
        )
        for row in json.loads(payload)
    ]


def contiguous_runs(days: list[date]) -> list[tuple[date, date]]:
    """Collapse sorted dates into [(start, end), ...] spans.

    Missing days are fetched as few requests as possible: a whole absent
    window becomes one call rather than seven.
    """
    runs: list[tuple[date, date]] = []
    for day in sorted(days):
        if runs and (day - runs[-1][1]).days == 1:
            runs[-1] = (runs[-1][0], day)
        else:
            runs.append((day, day))
    return runs


def cached_days(
    session: Session,
    provider: str,
    model: str,
    location_key: str,
    days: Iterable[date],
) -> dict[date, HourlyObservationCache]:
    """The subset of `days` already stored, keyed by local date."""
    wanted = {d.isoformat() for d in days}
    if not wanted:
        return {}
    rows = session.exec(
        select(HourlyObservationCache).where(
            HourlyObservationCache.provider == provider,
            HourlyObservationCache.model == model,
            HourlyObservationCache.location_key == location_key,
            HourlyObservationCache.local_date.in_(wanted),
        )
    ).all()
    return {date.fromisoformat(row.local_date): row for row in rows}


def store_days(
    session: Session,
    provider: str,
    model: str,
    location_key: str,
    result: ProviderResult,
    days: Iterable[date],
) -> None:
    """Cache one row per local day covered by a provider result.

    Days the provider returned nothing for are still stored, as an empty row:
    the gap is a fact about ERA5, not a transient miss, and caching it stops
    every later run re-asking for data that will never arrive.
    """
    by_day: dict[date, list[HourlyObservation]] = {d: [] for d in days}
    for observation in result.observations:
        if observation.local_date in by_day:
            by_day[observation.local_date].append(observation)

    existing = cached_days(session, provider, model, location_key, by_day)
    for day, observations in by_day.items():
        row = existing.get(day)
        if row is None:
            row = HourlyObservationCache(
                provider=provider, model=model,
                location_key=location_key, local_date=day.isoformat(),
                payload_json="[]",
            )
        row.payload_json = _serialize(observations)
        row.resolved_lat = result.resolved_lat
        row.resolved_lon = result.resolved_lon
        row.retrieved_at = datetime.now(timezone.utc)
        session.add(row)


def observations_for(
    session: Session,
    provider: WeatherArchiveProvider,
    location_key: str,
    lat: float,
    lon: float,
    tz_name: str,
    days: list[date],
) -> tuple[list[HourlyObservation], Optional[tuple[float, float]]]:
    """Observations for `days`, fetching only what is not already cached.

    Returns the observations in local-time order plus the resolved cell, which
    is None when every day came from the cache without a recorded resolution.
    """
    if not days:
        return [], None

    cached = cached_days(session, provider.provider, provider.model,
                         location_key, days)
    missing = [d for d in days if d not in cached]

    resolved: Optional[tuple[float, float]] = None
    for start, end in contiguous_runs(missing):
        span = [d for d in days if start <= d <= end]
        result = provider.fetch(lat, lon, tz_name, start, end)
        store_days(session, provider.provider, provider.model,
                   location_key, result, span)
        resolved = (result.resolved_lat, result.resolved_lon)

    if missing:
        session.commit()
        cached = cached_days(session, provider.provider, provider.model,
                             location_key, days)

    if resolved is None:
        for row in cached.values():
            if row.resolved_lat is not None:
                resolved = (row.resolved_lat, row.resolved_lon)
                break

    observations: list[HourlyObservation] = []
    for day in days:
        row = cached.get(day)
        if row is not None:
            observations.extend(observations_from_row(row.payload_json))
    observations.sort(key=lambda o: o.timestamp_utc)
    return observations, resolved
