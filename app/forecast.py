"""Leg forecast prefill: Open-Meteo forecast + geocoding clients, wind-text composer."""
from dataclasses import dataclass
from datetime import date
from typing import Optional

import httpx

from app.weather import ARCHIVE_URL, TIMEOUT_S, degrees_to_sector, knots_to_beaufort

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
DAILY_VARS = "sunrise,sunset"
HOURLY_VARS = "wind_speed_10m,wind_direction_10m"

# Day periods for the composed wind text: label -> local hours (inclusive).
# Night hours are skipped — the block describes the sailing day.
_PERIODS = (
    ("morning", range(6, 12)),
    ("afternoon", range(12, 18)),
    ("evening", range(18, 23)),
)


@dataclass
class LegForecast:
    """Prefill values for a leg's forecast block, in leg-local time."""
    sunrise: Optional[str] = None   # HH:MM
    sunset: Optional[str] = None    # HH:MM
    forecast: Optional[str] = None  # composed wind text, meant to be edited


@dataclass
class GeocodedPlace:
    lat: float
    lon: float
    label: str  # "Vodice, Slovenia" — shown to the user so a wrong hit is visible


def geocode_port(name: str, client: Optional[httpx.Client] = None) -> Optional[GeocodedPlace]:
    """Resolve a port name via Open-Meteo geocoding; None on miss.

    Place names are ambiguous across countries, so the caller should show
    the returned label and prefer known positions over geocoding.
    """
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=TIMEOUT_S)
    try:
        response = client.get(GEOCODING_URL, params={"name": name, "count": 1})
        response.raise_for_status()
        results = response.json().get("results")
    except httpx.HTTPError:
        return None
    finally:
        if own_client:
            client.close()
    if not results:
        return None
    hit = results[0]
    label = ", ".join(p for p in (hit.get("name"), hit.get("country")) if p)
    return GeocodedPlace(lat=hit["latitude"], lon=hit["longitude"], label=label)


def fetch_leg_forecast(
    leg_date: str,
    lat: float,
    lon: float,
    timezone: str,
    client: Optional[httpx.Client] = None,
    today: Optional[date] = None,
) -> Optional[LegForecast]:
    """Sunrise/sunset and a wind-forecast text for one day at one position.

    Today and future dates go to the forecast endpoint (covers ~15 days ahead);
    past dates to the archive endpoint, which also serves the daily sun times.
    """
    day = date.fromisoformat(leg_date)
    url = FORECAST_URL if day >= (today or date.today()) else ARCHIVE_URL
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": leg_date,
        "end_date": leg_date,
        "daily": DAILY_VARS,
        "hourly": HOURLY_VARS,
        "wind_speed_unit": "kn",
        "timezone": timezone,
    }

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=TIMEOUT_S)
    try:
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPError:
        return None
    finally:
        if own_client:
            client.close()

    daily = data.get("daily", {})
    hourly = data.get("hourly", {})
    hours = []
    for time_str, speed, direction in zip(
        hourly.get("time", []),
        hourly.get("wind_speed_10m") or [],
        hourly.get("wind_direction_10m") or [],
    ):
        if speed is not None and direction is not None:
            hours.append((int(time_str[11:13]), speed, direction))

    return LegForecast(
        sunrise=_daily_time(daily, "sunrise"),
        sunset=_daily_time(daily, "sunset"),
        forecast=compose_wind_text(hours),
    )


def _daily_time(daily: dict, variable: str) -> Optional[str]:
    values = daily.get(variable)
    if not values or not values[0]:
        return None
    return values[0][11:16]  # "2026-07-08T05:16" -> "05:16"


def compose_wind_text(hours: list[tuple[int, float, float]]) -> Optional[str]:
    """One editable sentence from (local hour, wind kn, direction deg) samples,
    e.g. "Morning NW 3-4 Bf, afternoon W 4 Bf, evening WNW 2-3 Bf"."""
    parts = []
    for label, period in _PERIODS:
        samples = [(kn, deg) for hour, kn, deg in hours if hour in period]
        if not samples:
            continue
        sectors = [degrees_to_sector(deg) for _, deg in samples]
        sector = max(set(sectors), key=sectors.count)
        forces = [knots_to_beaufort(kn) for kn, _ in samples]
        lo, hi = min(forces), max(forces)
        bf = str(lo) if lo == hi else f"{lo}-{hi}"
        parts.append((label, f"{sector} {bf} Bf"))
    if not parts:
        return None
    if len(parts) == len(_PERIODS) and len({wind for _, wind in parts}) == 1:
        return f"{parts[0][1]} all day"
    text = ", ".join(f"{label} {wind}" for label, wind in parts)
    return text[0].upper() + text[1:]
