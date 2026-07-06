"""Weather derivations and Open-Meteo client (PRD: weather enrichment).

HTTP is served by httpx.MockTransport from fixtures recorded off the real
Open-Meteo APIs (scripts/record_openmeteo_fixtures.py); no live calls.
"""
import json
import os
from datetime import datetime

import httpx

from app.weather import (
    cloud_pct_to_oktas,
    degrees_to_sector,
    fetch_weather,
    knots_to_beaufort,
    wave_height_to_douglas,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def _fixture(name: str):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def _client_serving(routes: dict, requests: list) -> httpx.Client:
    """Mock client mapping host substrings to fixture payloads."""
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        for host_part, payload in routes.items():
            if host_part in request.url.host:
                return httpx.Response(200, json=payload)
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


# The recorded archive fixture: Kornati day sail 2025-06-15, two locations.
KORNATI_POINTS = [
    (datetime(2025, 6, 15, 13, 20), 43.78, 15.30),  # loc 0, 13:00
    (datetime(2025, 6, 15, 14, 20), 43.79, 15.31),  # same grid cell, 14:00
    (datetime(2025, 6, 15, 14, 50), 43.90, 15.45),  # loc 1, 15:00
]


def test_fetch_batches_points_into_one_archive_request():
    requests = []
    client = _client_serving({"archive-api": _fixture("openmeteo_archive.json")}, requests)

    observations = fetch_weather(KORNATI_POINTS, client=client)

    archive_requests = [r for r in requests if "archive-api" in r.url.host]
    assert len(archive_requests) == 1
    params = archive_requests[0].url.params
    # nearby points collapse onto the same grid cell -> two locations, not three
    assert len(params["latitude"].split(",")) == 2
    assert params["cell_selection"] == "sea"
    assert params["wind_speed_unit"] == "kn"
    assert params["start_date"] == "2025-06-15"
    assert len(observations) == 3


def test_fetch_matches_each_point_to_own_location_and_hour():
    client = _client_serving({"archive-api": _fixture("openmeteo_archive.json")}, [])

    obs = fetch_weather(KORNATI_POINTS, client=client)

    # loc 0 @ 13:00: wind 10.0 kn / 263 deg, 23.8 C, 1016.6 hPa, 0 % cloud
    assert obs[0].wind_speed_kn == 10.0
    assert obs[0].wind_direction == "W"
    assert obs[0].wind_force == 3
    assert obs[0].air_temperature == 23.8
    assert obs[0].atmospheric_pressure == 1016.6
    assert obs[0].cloud_cover == 0
    # loc 0 @ 14:00 (13:50 rounds up): wind 10.9 kn / 259 deg
    assert obs[1].wind_speed_kn == 10.9
    assert obs[1].air_temperature == 23.7
    # loc 1 @ 15:00: wind 10.8 kn / 274 deg, 24.4 C, 7 % cloud -> 1 okta
    assert obs[2].wind_speed_kn == 10.8
    assert obs[2].wind_direction == "W"
    assert obs[2].air_temperature == 24.4
    assert obs[2].cloud_cover == 1


def test_moderate_breeze_is_4_bft():
    # WMO: 11-16 kn = force 4
    assert knots_to_beaufort(14.2) == 4


def test_beaufort_wmo_boundaries():
    # WMO ranges in knots: 0:<1, 1:1-3, 2:4-6, 3:7-10, 4:11-16, 5:17-21,
    # 6:22-27, 7:28-33, 8:34-40, 9:41-47, 10:48-55, 11:56-63, 12:>=64.
    assert knots_to_beaufort(0.0) == 0
    assert knots_to_beaufort(0.9) == 0
    assert knots_to_beaufort(1.0) == 1
    assert knots_to_beaufort(3.0) == 1
    assert knots_to_beaufort(4.0) == 2
    assert knots_to_beaufort(16.0) == 4    # top of force 4 stays 4
    assert knots_to_beaufort(16.4) == 4    # rounds to 16
    assert knots_to_beaufort(17.0) == 5
    assert knots_to_beaufort(63.0) == 11
    assert knots_to_beaufort(64.0) == 12
    assert knots_to_beaufort(120.0) == 12  # hurricane caps at 12


def test_cardinal_sectors_16_point():
    assert degrees_to_sector(0) == "N"
    assert degrees_to_sector(225) == "SW"
    assert degrees_to_sector(202.5) == "SSW"
    assert degrees_to_sector(45) == "NE"


def test_sector_boundaries_and_wrap():
    # Each sector is 22.5 deg wide, centred on its heading: N covers [348.75, 11.25).
    assert degrees_to_sector(11.24) == "N"
    assert degrees_to_sector(11.25) == "NNE"
    assert degrees_to_sector(348.75) == "N"
    assert degrees_to_sector(348.74) == "NNW"
    assert degrees_to_sector(360) == "N"


def test_cloud_pct_to_oktas():
    assert cloud_pct_to_oktas(0) == 0
    assert cloud_pct_to_oktas(100) == 8
    assert cloud_pct_to_oktas(50) == 4
    assert cloud_pct_to_oktas(15) == 1
    assert cloud_pct_to_oktas(19) == 2   # 1.52 rounds up


def test_wave_height_to_douglas():
    # Douglas scale by significant wave height (m), lower bound inclusive:
    # 0: 0, 1: (0-0.1], 2: (0.1-0.5], 3: (0.5-1.25], 4: (1.25-2.5],
    # 5: (2.5-4], 6: (4-6], 7: (6-9], 8: (9-14], 9: >14
    assert wave_height_to_douglas(0.0) == 0
    assert wave_height_to_douglas(0.05) == 1
    assert wave_height_to_douglas(0.3) == 2
    assert wave_height_to_douglas(0.5) == 2
    assert wave_height_to_douglas(1.0) == 3
    assert wave_height_to_douglas(2.5) == 4
    assert wave_height_to_douglas(3.0) == 5
    assert wave_height_to_douglas(5.0) == 6
    assert wave_height_to_douglas(8.0) == 7
    assert wave_height_to_douglas(10.0) == 8
    assert wave_height_to_douglas(15.0) == 9
