"""Leg forecast client: Open-Meteo forecast/geocoding + wind-text composition.

HTTP is served by httpx.MockTransport from fixtures recorded off the real
APIs (scripts/record_forecast_fixtures.py); no live calls.
"""
import json
import os
from datetime import date, timedelta

import httpx

from app.forecast import compose_wind_text, fetch_leg_forecast, geocode_port

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


TODAY = date(2026, 7, 7)  # matches the recorded forecast fixture (2026-07-08)


# --- geocoding ---

def test_geocode_resolves_port_with_country_label():
    client = _client_serving({"geocoding-api": _fixture("openmeteo_geocoding.json")}, [])

    place = geocode_port("Vodice", client=client)

    # The recorded hit is Vodice in *Slovenia*, not the Croatian port — the
    # label carries the country exactly so the user can spot a wrong hit.
    assert place.lat == 46.18987
    assert place.lon == 14.49492
    assert place.label == "Vodice, Slovenia"


def test_geocode_miss_returns_none():
    client = _client_serving({"geocoding-api": _fixture("openmeteo_geocoding_miss.json")}, [])
    assert geocode_port("Xyzzyport", client=client) is None


def test_geocode_http_failure_returns_none():
    client = _client_serving({}, [])  # every host answers 404
    assert geocode_port("Vodice", client=client) is None


# --- fetch_leg_forecast ---

def test_future_date_uses_forecast_endpoint():
    requests = []
    client = _client_serving({"api.open-meteo": _fixture("openmeteo_forecast.json")}, requests)

    fc = fetch_leg_forecast("2026-07-08", 43.78, 15.30, "Europe/Zagreb",
                            client=client, today=TODAY)

    assert requests[0].url.host == "api.open-meteo.com"
    params = requests[0].url.params
    assert params["daily"] == "sunrise,sunset"
    assert params["wind_speed_unit"] == "kn"
    assert params["timezone"] == "Europe/Zagreb"
    assert fc.sunrise == "05:25"
    assert fc.sunset == "20:42"
    # exact wording is compose_wind_text's business — here just: it's a wind text
    assert "Bf" in fc.forecast


def test_past_date_uses_archive_endpoint():
    requests = []
    client = _client_serving({"archive-api": _fixture("openmeteo_archive_forecast.json")}, requests)

    fc = fetch_leg_forecast("2025-06-15", 43.78, 15.30, "Europe/Zagreb",
                            client=client, today=TODAY)

    assert requests[0].url.host == "archive-api.open-meteo.com"
    assert fc.sunrise == "05:15"
    assert fc.sunset == "20:42"
    assert "Bf" in fc.forecast


def test_today_counts_as_forecast_not_archive():
    requests = []
    client = _client_serving({"api.open-meteo": _fixture("openmeteo_forecast.json")}, requests)

    fetch_leg_forecast(TODAY.isoformat(), 43.78, 15.30, "Europe/Zagreb",
                       client=client, today=TODAY)

    assert requests[0].url.host == "api.open-meteo.com"


def test_http_failure_returns_none():
    client = _client_serving({}, [])
    assert fetch_leg_forecast("2026-07-08", 43.78, 15.30, "Europe/Zagreb",
                              client=client, today=TODAY) is None


def test_network_timeout_returns_none():
    def handler(request):
        raise httpx.ConnectTimeout("boom")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_leg_forecast("2026-07-08", 43.78, 15.30, "Europe/Zagreb",
                              client=client, today=TODAY) is None


# --- compose_wind_text ---

def _hours(spec: dict) -> list[tuple[int, float, float]]:
    return [(hour, kn, deg) for hour, (kn, deg) in spec.items()]


def test_wind_text_describes_each_period():
    hours = _hours({
        6: (5, 90), 8: (6, 95), 10: (6.5, 100),    # morning E, 2 Bf
        12: (12, 225), 14: (17, 230), 16: (16, 220),  # afternoon SW, 4-5 Bf
        19: (8, 270), 21: (9, 275),                  # evening W, 3 Bf
    })
    assert compose_wind_text(hours) == "Morning E 2 Bf, afternoon SW 4-5 Bf, evening W 3 Bf"


def test_steady_wind_collapses_to_all_day():
    hours = [(h, 12.0, 315.0) for h in range(6, 23)]
    assert compose_wind_text(hours) == "NW 4 Bf all day"


def test_missing_periods_are_skipped():
    hours = _hours({13: (14, 225), 15: (15, 230)})
    assert compose_wind_text(hours) == "Afternoon SW 4 Bf"


def test_night_only_or_empty_yields_none():
    assert compose_wind_text([(2, 10.0, 180.0), (23, 12.0, 200.0)]) is None
    assert compose_wind_text([]) is None
