"""CSV and JSON exports for an analysis run (PRD §10).

Every export carries the source, model, and the exact thresholds used, plus
the reanalysis disclaimer (§11).
"""
import csv
import io
import json
from typing import Iterable, Optional

from sqlmodel import Session

from app.analysis.cache import cached_days, observations_from_row
from app.analysis.runner import ATTRIBUTION, AnalysisResult, snapshots_of
from app.analysis.window import describe

HOURLY_COLUMNS = (
    "timestamp_utc", "local_timestamp", "location",
    "requested_lat", "requested_lon", "resolved_lat", "resolved_lon",
    "wind_speed_kn", "wind_direction_deg", "wind_gust_kn",
    "precipitation_mm", "pressure_msl_hpa", "provider", "model",
)

DAILY_COLUMNS = (
    "date", "location", "avg_wind_kn", "max_wind_kn", "max_gust_kn",
    "rain_total_mm", "dominant_direction", "sailing_rating", "lost_day",
    "provider", "model",
)


def _metadata_lines(result: AnalysisResult) -> list[str]:
    """Leading comment lines: provenance and the thresholds behind the values."""
    run = result.run
    thresholds = ", ".join(f"{k}={v}" for k, v in
                           sorted(result.thresholds.as_dict().items()))
    return [
        f"# Ship Log weather analysis — run {run.id}",
        f"# window: {describe(run.start_month, run.start_day, run.end_month, run.end_day)}"
        f", years {run.first_year}-{run.last_year}",
        f"# source: {run.provider} / {run.model}",
        f"# thresholds: {thresholds}",
        f"# {ATTRIBUTION}",
    ]


def _writer(columns: Iterable[str], result: AnalysisResult):
    buffer = io.StringIO()
    for line in _metadata_lines(result):
        buffer.write(line + "\n")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    return buffer, writer


def daily_csv(result: AnalysisResult) -> str:
    """One row per location-day (PRD §10)."""
    buffer, writer = _writer(DAILY_COLUMNS, result)
    run = result.run
    names = {s["key"]: s["name"] for s in snapshots_of(run)}

    for key, days in result.daily.items():
        for day in days:
            writer.writerow([
                day.local_date.isoformat(),
                names.get(key, key),
                day.avg_wind_kn if day.avg_wind_kn is not None else "",
                day.max_wind_kn if day.max_wind_kn is not None else "",
                day.max_gust_kn if day.max_gust_kn is not None else "",
                day.rain_total_mm if day.rain_total_mm is not None else "",
                day.dominant_direction or "",
                day.sailing_rating.value if day.sailing_rating else "",
                "true" if day.lost_day else "false",
                run.provider,
                run.model,
            ])
    return buffer.getvalue()


def hourly_csv(session: Session, result: AnalysisResult) -> str:
    """One row per location-hour, read back from the observation cache.

    The run stores only daily summaries, so the hourly detail comes from the
    cache using the run's own window — which is exactly the data the run was
    computed from, since ERA5 values never change.
    """
    buffer, writer = _writer(HOURLY_COLUMNS, result)
    run = result.run
    days = [d for window in result.windows for d in window.dates]

    for snapshot in snapshots_of(run):
        key = snapshot["key"]
        rows = cached_days(session, run.provider, run.model, key, days)
        for day in sorted(rows):
            row = rows[day]
            for observation in observations_from_row(row.payload_json):
                writer.writerow([
                    observation.timestamp_utc.isoformat(),
                    observation.local_timestamp.isoformat(),
                    snapshot["name"],
                    snapshot["latitude"],
                    snapshot["longitude"],
                    row.resolved_lat if row.resolved_lat is not None else "",
                    row.resolved_lon if row.resolved_lon is not None else "",
                    _num(observation.wind_speed_kn),
                    _num(observation.wind_direction_deg),
                    _num(observation.wind_gust_kn),
                    _num(observation.precipitation_mm),
                    _num(observation.pressure_msl_hpa),
                    run.provider,
                    run.model,
                ])
    return buffer.getvalue()


def _num(value: Optional[float]):
    return "" if value is None else value


def summary_json(result: AnalysisResult) -> str:
    """Run metadata plus the §10 statistical body, per location."""
    run = result.run
    payload = {
        "run": {
            "id": run.id,
            "created_at": run.created_at.isoformat(),
            "window": {
                "start_month": run.start_month, "start_day": run.start_day,
                "end_month": run.end_month, "end_day": run.end_day,
                "label": describe(run.start_month, run.start_day,
                                  run.end_month, run.end_day),
            },
            "years": {"first": run.first_year, "last": run.last_year},
            "provider": run.provider,
            "model": run.model,
            "thresholds": result.thresholds.as_dict(),
            "locations": snapshots_of(run),
        },
        "attribution": ATTRIBUTION,
        "comparison": result.statement,
        "locations": {
            summary.location_key: {
                "name": summary.name,
                "region": summary.region,
                "days_analyzed": summary.days,
                "windows_analyzed": summary.windows,
                "green_percent": summary.green_pct,
                "yellow_percent": summary.yellow_pct,
                "red_percent": summary.red_pct,
                "lost_day_probability": summary.lost_day_probability,
                "mean_lost_days_per_window": summary.mean_lost_days,
                "median_max_wind_kn": summary.median_max_wind_kn,
                "median_daily_rain_mm": summary.median_rain_mm,
                **summary.statistics,
            }
            for summary in result.ranked
        },
    }
    return json.dumps(payload, indent=2)


def export_filename(result: AnalysisResult, kind: str, suffix: str) -> str:
    """e.g. "weather-analysis_24Oct-30Oct_2010-2024_daily.csv"."""
    run = result.run
    window = describe(run.start_month, run.start_day,
                      run.end_month, run.end_day).replace(" ", "")
    return (f"weather-analysis_{window}_{run.first_year}-{run.last_year}"
            f"_{kind}.{suffix}")
