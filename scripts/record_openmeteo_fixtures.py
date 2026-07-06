"""Record real Open-Meteo API responses as test fixtures.

Run from the repo root (needs network, no credentials):

    python scripts/record_openmeteo_fixtures.py

Writes:
    tests/fixtures/openmeteo_archive.json        (2 locations, one past day)
    tests/fixtures/openmeteo_marine.json         (wave height, 2 locations)

Locations/date match the tests: a Kornati day sail on 2025-06-15.
"""
import json
import os

import httpx

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")

HOURLY = "wind_speed_10m,wind_direction_10m,temperature_2m,pressure_msl,cloud_cover"
LATS, LONS = "43.78,43.90", "15.30,15.45"
SAIL_DAY = "2025-06-15"


def _get(url: str, **params) -> object:
    r = httpx.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    os.makedirs(FIXTURE_DIR, exist_ok=True)

    fixtures = {
        "openmeteo_archive.json": _get(
            "https://archive-api.open-meteo.com/v1/archive",
            latitude=LATS, longitude=LONS,
            start_date=SAIL_DAY, end_date=SAIL_DAY,
            hourly=HOURLY, wind_speed_unit="kn",
            cell_selection="sea", timezone="UTC",
        ),
        "openmeteo_marine.json": _get(
            "https://marine-api.open-meteo.com/v1/marine",
            latitude=LATS, longitude=LONS,
            start_date=SAIL_DAY, end_date=SAIL_DAY,
            hourly="wave_height", cell_selection="sea", timezone="UTC",
        ),
    }

    for name, payload in fixtures.items():
        path = os.path.join(FIXTURE_DIR, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
