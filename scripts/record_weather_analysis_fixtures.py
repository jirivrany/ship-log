"""Record real Open-Meteo archive responses as weather-analysis test fixtures.

Run from the repo root (needs network, no credentials):

    python scripts/record_weather_analysis_fixtures.py

Writes:
    tests/fixtures/analysis_archive_murter.json    (7-day window, one location)
    tests/fixtures/analysis_archive_dst.json       (window crossing the DST end)

The requests mirror app.analysis.provider exactly: ERA5, sea cell selection,
knots, UTC timestamps (the provider converts to local itself).
"""
import json
import os

import httpx

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")

HOURLY = "wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation,pressure_msl"

# Murtersko More, the PRD's Middle Adriatic sample point.
MURTER_LAT, MURTER_LON = 43.82, 15.60


def _archive(**params) -> object:
    r = httpx.get(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "wind_speed_unit": "kn",
            "cell_selection": "sea",
            "models": "era5",
            "timezone": "UTC",
            **params,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def main():
    os.makedirs(FIXTURE_DIR, exist_ok=True)

    fixtures = {
        # The first offered scenario: October 24-30, padded by a day on each
        # side exactly as the provider pads it.
        "analysis_archive_murter.json": _archive(
            latitude=MURTER_LAT, longitude=MURTER_LON,
            start_date="2024-10-23", end_date="2024-10-31",
            hourly=HOURLY,
        ),
        # EU summer time ends 2024-10-27: local 02:00 occurs twice and the
        # local day has 25 hours.
        "analysis_archive_dst.json": _archive(
            latitude=MURTER_LAT, longitude=MURTER_LON,
            start_date="2024-10-25", end_date="2024-10-29",
            hourly=HOURLY,
        ),
    }

    for name, payload in fixtures.items():
        path = os.path.join(FIXTURE_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
