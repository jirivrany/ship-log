"""Record real API responses for the leg-forecast fixtures.

Run from the repo root (needs network, no credentials):

    python scripts/record_forecast_fixtures.py

Writes:
    tests/fixtures/openmeteo_forecast.json          (forecast endpoint, one day)
    tests/fixtures/openmeteo_archive_forecast.json  (archive endpoint, past day)
    tests/fixtures/openmeteo_geocoding.json         (geocoding hit: Vodice)
    tests/fixtures/openmeteo_geocoding_miss.json    (geocoding miss)
    tests/fixtures/ecmwf_product.json               (Open Charts product metadata)

Location matches the weather fixtures: Kornati, past day 2025-06-15; the
forecast-endpoint day is whatever tomorrow is at recording time (the payload
shape, not the date, is what the tests depend on).
"""
import datetime
import json
import os

import httpx

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")

LAT, LON = "43.78", "15.30"
PAST_SAIL_DAY = "2025-06-15"
FORECAST_PARAMS = dict(
    latitude=LAT, longitude=LON,
    daily="sunrise,sunset", hourly="wind_speed_10m,wind_direction_10m",
    wind_speed_unit="kn", timezone="Europe/Zagreb",
)


def _get(url: str, **params) -> object:
    r = httpx.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    os.makedirs(FIXTURE_DIR, exist_ok=True)
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()

    fixtures = {
        "openmeteo_forecast.json": _get(
            "https://api.open-meteo.com/v1/forecast",
            start_date=tomorrow, end_date=tomorrow, **FORECAST_PARAMS,
        ),
        "openmeteo_archive_forecast.json": _get(
            "https://archive-api.open-meteo.com/v1/archive",
            start_date=PAST_SAIL_DAY, end_date=PAST_SAIL_DAY, **FORECAST_PARAMS,
        ),
        "openmeteo_geocoding.json": _get(
            "https://geocoding-api.open-meteo.com/v1/search",
            name="Vodice", count=1,
        ),
        "openmeteo_geocoding_miss.json": _get(
            "https://geocoding-api.open-meteo.com/v1/search",
            name="Xyzzyport", count=1,
        ),
        "ecmwf_product.json": _get(
            "https://charts.ecmwf.int/opencharts-api/v1/products/medium-mslp-wind850/",
            valid_time=f"{tomorrow}T12:00:00Z",
            projection="opencharts_south_east_europe",
        ),
    }

    for name, payload in fixtures.items():
        path = os.path.join(FIXTURE_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
