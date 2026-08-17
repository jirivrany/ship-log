"""CSV and JSON exports (PRD: weather analysis §10, AC8)."""
import csv
import io
import json
from datetime import date, datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis.aggregate import Thresholds
from app.analysis.exports import (
    DAILY_COLUMNS,
    HOURLY_COLUMNS,
    daily_csv,
    export_filename,
    hourly_csv,
    summary_json,
)
from app.analysis.provider import HourlyObservation, ProviderResult
from app.analysis.runner import AnalysisRequest, run_analysis
from app.analysis.seed import seed_locations
from app.models import AnalysisLocation


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        seed_locations(s)
        yield s


class FakeProvider:
    provider = "open-meteo"
    model = "era5"

    def fetch(self, lat, lon, tz_name, start, end):
        observations = []
        day = start
        while day <= end:
            for hour in (0, 12):
                ts = datetime(day.year, day.month, day.day, hour,
                              tzinfo=timezone.utc)
                observations.append(HourlyObservation(
                    timestamp_utc=ts, local_timestamp=ts,
                    wind_speed_kn=12.5, wind_direction_deg=90.0,
                    wind_gust_kn=18.0, precipitation_mm=0.5,
                    pressure_msl_hpa=1013.0,
                ))
            day = date.fromordinal(day.toordinal() + 1)
        return ProviderResult(
            observations=observations, resolved_lat=43.75, resolved_lon=15.5,
            provider=self.provider, model=self.model,
            retrieved_at=datetime.now(timezone.utc),
        )


@pytest.fixture
def result(session):
    ids = [session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == key)
    ).first().id for key in ("murtersko_more", "hvar")]
    return run_analysis(session, AnalysisRequest(
        location_ids=ids, start_month=10, start_day=24,
        end_month=10, end_day=26, first_year=2023, last_year=2024,
        thresholds=Thresholds(),
    ), FakeProvider())


def _rows(text: str) -> tuple[list[str], list[list[str]]]:
    """Split an export into its metadata comments and parsed CSV rows."""
    lines = text.splitlines()
    comments = [l for l in lines if l.startswith("#")]
    body = [l for l in lines if not l.startswith("#")]
    return comments, list(csv.reader(io.StringIO("\n".join(body))))


# --- daily CSV --------------------------------------------------------------

def test_daily_csv_header_matches_the_prd(result):
    _, rows = _rows(daily_csv(result))

    assert rows[0] == list(DAILY_COLUMNS)
    assert rows[0] == [
        "date", "location", "avg_wind_kn", "max_wind_kn", "max_gust_kn",
        "rain_total_mm", "dominant_direction", "sailing_rating", "lost_day",
        "provider", "model",
    ]


def test_daily_csv_has_one_row_per_location_day(result):
    _, rows = _rows(daily_csv(result))

    # 2 locations x 2 years x 3 days
    assert len(rows) - 1 == 12


def test_daily_csv_values_are_normalized(result):
    _, rows = _rows(daily_csv(result))
    header, first = rows[0], rows[1]
    row = dict(zip(header, first))

    assert row["date"] == "2023-10-24"
    assert row["location"] in {"Murtersko More", "Hvar"}
    assert row["max_wind_kn"] == "12.5"
    assert row["dominant_direction"] == "E"
    assert row["sailing_rating"] == "green"
    assert row["lost_day"] == "false"
    assert (row["provider"], row["model"]) == ("open-meteo", "era5")


# --- hourly CSV -------------------------------------------------------------

def test_hourly_csv_header_matches_the_prd(session, result):
    _, rows = _rows(hourly_csv(session, result))

    assert rows[0] == list(HOURLY_COLUMNS)
    assert rows[0] == [
        "timestamp_utc", "local_timestamp", "location",
        "requested_lat", "requested_lon", "resolved_lat", "resolved_lon",
        "wind_speed_kn", "wind_direction_deg", "wind_gust_kn",
        "precipitation_mm", "pressure_msl_hpa", "provider", "model",
    ]


def test_hourly_csv_carries_requested_and_resolved_coordinates(session, result):
    _, rows = _rows(hourly_csv(session, result))
    row = dict(zip(rows[0], rows[1]))

    assert row["requested_lat"] in {"43.82", "43.15"}
    assert row["resolved_lat"] == "43.75"
    assert row["resolved_lon"] == "15.5"
    # both timestamps are present and distinguishable
    assert row["timestamp_utc"].startswith("2023-10-24T")
    assert row["local_timestamp"].startswith("2023-10-24T")


def test_hourly_csv_covers_every_cached_hour(session, result):
    _, rows = _rows(hourly_csv(session, result))

    # 2 locations x 2 years x 3 days x 2 hours
    assert len(rows) - 1 == 24


def test_hourly_csv_reads_back_the_run_that_produced_it(session, result):
    """The run stores only daily summaries; hourly detail comes from the cache
    and must still line up with the run's window."""
    _, rows = _rows(hourly_csv(session, result))
    dates = {r[0][:10] for r in rows[1:]}

    assert dates == {"2023-10-24", "2023-10-25", "2023-10-26",
                     "2024-10-24", "2024-10-25", "2024-10-26"}


# --- metadata on every export ----------------------------------------------

@pytest.mark.parametrize("export", ["daily", "hourly"])
def test_csv_exports_carry_source_model_and_thresholds(session, result, export):
    text = (daily_csv(result) if export == "daily"
            else hourly_csv(session, result))
    comments, _ = _rows(text)
    blob = "\n".join(comments)

    assert "open-meteo" in blob and "era5" in blob
    assert "yellow_wind_kn=20.0" in blob
    assert "red_gust_kn=40.0" in blob
    assert "lost_rain_mm=20.0" in blob
    # PRD §11: the reanalysis disclaimer travels with the data
    assert "reanalysis" in blob.lower()
    assert "not observations" in blob


def test_custom_thresholds_appear_in_the_export(session):
    location = session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == "hvar")
    ).first()
    result = run_analysis(session, AnalysisRequest(
        location_ids=[location.id], start_month=10, start_day=24,
        end_month=10, end_day=24, first_year=2024, last_year=2024,
        thresholds=Thresholds(yellow_wind_kn=15.0),
    ), FakeProvider())

    comments, _ = _rows(daily_csv(result))

    assert "yellow_wind_kn=15.0" in "\n".join(comments)


# --- summary JSON -----------------------------------------------------------

def test_summary_json_has_the_documented_statistical_keys(result):
    payload = json.loads(summary_json(result))

    for location in payload["locations"].values():
        assert {"wind_bins", "direction_distribution", "rainfall_statistics",
                "sailing_categories", "lost_day_statistics"} <= location.keys()


def test_summary_json_carries_run_metadata(result):
    payload = json.loads(summary_json(result))

    run = payload["run"]
    assert run["provider"] == "open-meteo"
    assert run["model"] == "era5"
    assert run["years"] == {"first": 2023, "last": 2024}
    assert run["window"]["label"] == "24 Oct - 26 Oct"
    assert run["thresholds"]["yellow_wind_kn"] == 20.0
    assert len(run["locations"]) == 2
    # AC9: attribution travels with the export
    assert "Open-Meteo" in payload["attribution"]
    assert "ERA5" in payload["attribution"]


def test_summary_json_includes_the_qualified_comparison(result):
    payload = json.loads(summary_json(result))

    assert "not a forecast" in payload["comparison"]


def test_summary_json_location_snapshots_hold_coordinates(result):
    payload = json.loads(summary_json(result))

    snapshot = payload["run"]["locations"][0]
    assert {"key", "name", "region", "latitude", "longitude",
            "timezone"} <= snapshot.keys()


def test_summary_json_is_ordered_best_first(result):
    payload = json.loads(summary_json(result))

    keys = list(payload["locations"])
    greens = [payload["locations"][k]["green_percent"] for k in keys]
    assert greens == sorted(greens, reverse=True)


# --- filenames --------------------------------------------------------------

def test_export_filenames_describe_the_run(result):
    assert export_filename(result, "daily", "csv") == (
        "weather-analysis_24Oct-26Oct_2023-2024_daily.csv")
    assert export_filename(result, "hourly", "csv").endswith("_hourly.csv")
    assert export_filename(result, "summary", "json").endswith("_summary.json")
