"""Write policy of the weather enrichment applier (PRD: weather enrichment).

The applier owns the rules that guard the skipper's own observations:
fill-empty by default, explicit overwrite, provenance marking.
"""
from datetime import datetime

from app.models import EntrySource, LogEntry
from app.weather import WeatherObservation
from app.weather_apply import apply_weather


def _entry(**overrides) -> LogEntry:
    defaults = dict(
        leg_id=1,
        timestamp=datetime(2026, 6, 15, 14, 20),
        lat=43.78,
        lon=15.30,
        source=EntrySource.turning_point,
    )
    defaults.update(overrides)
    return LogEntry(**defaults)


def _observation(**overrides) -> WeatherObservation:
    defaults = dict(
        wind_speed_kn=14.2,
        wind_direction="NE",
        wind_force=4,
        air_temperature=24.5,
        atmospheric_pressure=1014.5,
        cloud_cover=1,
        sea_state=3,
    )
    defaults.update(overrides)
    return WeatherObservation(**defaults)


def test_fills_empty_fields_and_marks_source():
    entry = _entry()
    filled = apply_weather([entry], [_observation()], overwrite=False)

    assert filled == 1
    assert entry.wind_speed_kn == 14.2
    assert entry.wind_direction == "NE"
    assert entry.wind_force == 4
    assert entry.air_temperature == 24.5
    assert entry.atmospheric_pressure == 1014.5
    assert entry.cloud_cover == 1
    assert entry.sea_state == 3
    assert entry.weather_source == "open-meteo"
