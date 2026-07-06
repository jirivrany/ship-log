"""Write policy for weather enrichment: which fetched values may land where.

Kept free of I/O so the rules that protect the skipper's own observations
can be tested in isolation.
"""
from typing import Optional

from app.models import LogEntry
from app.weather import WeatherObservation

WEATHER_SOURCE = "open-meteo"

# LogEntry column -> WeatherObservation attribute (same names by design)
WEATHER_FIELDS = (
    "wind_speed_kn",
    "wind_direction",
    "wind_force",
    "air_temperature",
    "atmospheric_pressure",
    "cloud_cover",
    "sea_state",
)


def apply_weather(
    entries: list[LogEntry],
    observations: list[Optional[WeatherObservation]],
    overwrite: bool,
) -> int:
    """Write observations onto their entries (parallel lists); returns how
    many entries received at least one value."""
    filled = 0
    for entry, obs in zip(entries, observations):
        if obs is None:
            continue
        wrote = False
        for field in WEATHER_FIELDS:
            value = getattr(obs, field)
            if value is None:
                continue
            if getattr(entry, field) is None or overwrite:
                setattr(entry, field, value)
                wrote = True
        if wrote:
            entry.weather_source = WEATHER_SOURCE
            filled += 1
    return filled
