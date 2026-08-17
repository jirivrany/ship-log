"""Analysis orchestration: validate, fetch/cache, aggregate, persist (PRD §7).

An AnalysisRun records the question asked; the observations behind it live in
the shared cache. Persisting a location snapshot means a later edit or removal
cannot silently change what an existing result page means.
"""
import json
from dataclasses import dataclass
from datetime import date
from typing import Optional

from sqlmodel import Session, delete, select

from app.analysis import locations as locations_mod
from app.analysis.aggregate import DailyAggregate, Thresholds, aggregate_days
from app.analysis.cache import observations_for
from app.analysis.provider import (
    OpenMeteoArchiveProvider,
    ProviderError,
    WeatherArchiveProvider,
)
from app.analysis.statistics import (
    LocationSummary,
    comparison_statement,
    rank,
    summarize_location,
)
from app.analysis.window import AnalysisWindow, WindowError, expand_window
from app.models import AnalysisLocation, AnalysisRun, DailySummary, SailingRating

# PRD §3/§11: the page and every export must carry this.
ATTRIBUTION = (
    "Historical weather from Open-Meteo (ERA5 reanalysis). "
    "Reanalysis values are model estimates for a grid cell, not observations "
    "made at the named location."
)


@dataclass(frozen=True)
class AnalysisRequest:
    location_ids: list[int]
    start_month: int
    start_day: int
    end_month: int
    end_day: int
    first_year: int
    last_year: int
    thresholds: Thresholds


@dataclass
class AnalysisResult:
    """A finished run, ready for the result page and the exports."""
    run: AnalysisRun
    windows: list[AnalysisWindow]
    summaries: list[LocationSummary]
    daily: dict[str, list[DailyAggregate]]   # location key -> days
    thresholds: Thresholds
    statement: Optional[str]

    @property
    def ranked(self) -> list[LocationSummary]:
        return rank(self.summaries)


def _snapshot(location: AnalysisLocation) -> dict:
    return {
        "key": location.key,
        "name": location.name,
        "region": location.region,
        "latitude": location.latitude,
        "longitude": location.longitude,
        "timezone": location.timezone,
        "resolved_lat": location.resolved_lat,
        "resolved_lon": location.resolved_lon,
    }


def run_analysis(
    session: Session,
    request: AnalysisRequest,
    provider: Optional[WeatherArchiveProvider] = None,
) -> AnalysisResult:
    """Execute an analysis and persist it.

    Raises WindowError for an invalid window and ProviderError when data
    cannot be obtained; neither leaves a successful-looking run behind.
    """
    provider = provider or OpenMeteoArchiveProvider()

    windows = expand_window(
        request.start_month, request.start_day,
        request.end_month, request.end_day,
        request.first_year, request.last_year,
    )
    if not request.location_ids:
        raise WindowError("Select at least one location")

    # Ordered by the picker's ordering, not by the order ids arrived in.
    selected = list(session.exec(
        select(AnalysisLocation)
        .where(AnalysisLocation.id.in_(request.location_ids))
        .order_by(AnalysisLocation.region, AnalysisLocation.name)
    ).all())
    if not selected:
        raise WindowError("Select at least one location")

    all_days: list[date] = [d for w in windows for d in w.dates]

    daily: dict[str, list[DailyAggregate]] = {}
    summaries: list[LocationSummary] = []

    try:
        for location in selected:
            observations, resolved = observations_for(
                session, provider, location.key,
                location.latitude, location.longitude, location.timezone,
                all_days,
            )
            if resolved:
                locations_mod.record_resolution(session, location.key, *resolved)

            # Aggregate per window so each day carries the year that identifies
            # its window — a window crossing New Year spans two calendar years.
            days: list[DailyAggregate] = []
            for window in windows:
                wanted = set(window.dates)
                days.extend(aggregate_days(
                    [o for o in observations if o.local_date in wanted],
                    window_year=window.year,
                    thresholds=request.thresholds,
                ))
            days.sort(key=lambda d: d.local_date)
            daily[location.key] = days
            summaries.append(summarize_location(
                location.key, location.name, location.region,
                days, request.thresholds,
            ))
    finally:
        # The provider pools one connection across the whole run; release it
        # whether the run succeeded or a fetch failed part-way through.
        close = getattr(provider, "close", None)
        if callable(close):
            close()

    run = AnalysisRun(
        start_month=request.start_month, start_day=request.start_day,
        end_month=request.end_month, end_day=request.end_day,
        first_year=request.first_year, last_year=request.last_year,
        provider=provider.provider, model=provider.model,
        thresholds_json=json.dumps(request.thresholds.as_dict()),
        location_snapshots_json=json.dumps([_snapshot(l) for l in selected]),
        status="ok",
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    for key, days in daily.items():
        for day in days:
            session.add(DailySummary(
                run_id=run.id,
                location_key=key,
                local_date=day.local_date.isoformat(),
                window_year=day.window_year,
                avg_wind_kn=day.avg_wind_kn,
                max_wind_kn=day.max_wind_kn,
                max_gust_kn=day.max_gust_kn,
                rain_total_mm=day.rain_total_mm,
                dominant_direction=day.dominant_direction,
                sailing_rating=day.sailing_rating,
                lost_day=day.lost_day,
            ))
    session.commit()

    return AnalysisResult(
        run=run, windows=windows, summaries=summaries, daily=daily,
        thresholds=request.thresholds,
        statement=comparison_statement(summaries),
    )


def load_result(session: Session, run_id: int) -> Optional[AnalysisResult]:
    """Rebuild a stored run's result from its daily summaries.

    Reads the location snapshot rather than the live location, so an edited or
    removed location does not rewrite history (PRD §5).
    """
    run = session.get(AnalysisRun, run_id)
    if run is None:
        return None

    thresholds = Thresholds.from_dict(json.loads(run.thresholds_json))
    snapshots = json.loads(run.location_snapshots_json)
    rows = session.exec(
        select(DailySummary)
        .where(DailySummary.run_id == run_id)
        .order_by(DailySummary.local_date)
    ).all()

    daily: dict[str, list[DailyAggregate]] = {s["key"]: [] for s in snapshots}
    for row in rows:
        daily.setdefault(row.location_key, []).append(DailyAggregate(
            local_date=date.fromisoformat(row.local_date),
            window_year=row.window_year,
            avg_wind_kn=row.avg_wind_kn,
            max_wind_kn=row.max_wind_kn,
            max_gust_kn=row.max_gust_kn,
            rain_total_mm=row.rain_total_mm,
            dominant_direction=row.dominant_direction,
            sailing_rating=(SailingRating(row.sailing_rating)
                            if row.sailing_rating else None),
            lost_day=row.lost_day,
            hours=0,          # hourly detail lives in the cache, not the run
        ))

    summaries = [
        summarize_location(s["key"], s["name"], s["region"],
                           daily.get(s["key"], []), thresholds)
        for s in snapshots
    ]
    windows = expand_window(run.start_month, run.start_day,
                            run.end_month, run.end_day,
                            run.first_year, run.last_year)

    return AnalysisResult(
        run=run, windows=windows, summaries=summaries, daily=daily,
        thresholds=thresholds, statement=comparison_statement(summaries),
    )


def snapshots_of(run: AnalysisRun) -> list[dict]:
    return json.loads(run.location_snapshots_json)


def location_label(run: AnalysisRun, limit: int = 4) -> str:
    """Names of the analysed locations, e.g. "Hvar, Kornati +2 more".

    Runs over the same window and years are otherwise indistinguishable in the
    history list, so the locations are what tells them apart. Reads the run's
    snapshot, so a since-renamed location shows the name it was analysed under.
    """
    names = [s.get("name") or s.get("key", "?") for s in snapshots_of(run)]
    if not names:
        return "no locations"
    if len(names) <= limit:
        return ", ".join(names)
    return f"{', '.join(names[:limit])} +{len(names) - limit} more"


def recent_runs(session: Session, limit: int = 20) -> list[AnalysisRun]:
    return list(session.exec(
        select(AnalysisRun).order_by(AnalysisRun.created_at.desc()).limit(limit)
    ).all())


def delete_run(session: Session, run_id: int) -> None:
    """Drop a run and its summaries; cached observations are kept (§7)."""
    session.exec(delete(DailySummary).where(DailySummary.run_id == run_id))
    run = session.get(AnalysisRun, run_id)
    if run is not None:
        session.delete(run)
    session.commit()
