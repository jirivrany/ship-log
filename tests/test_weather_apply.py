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


def test_default_never_touches_existing_values():
    # Garmin-measured temp + import-prefilled wind must survive a default fetch,
    # while the still-empty fields around them get filled.
    entry = _entry(air_temperature=22.0, wind_force=3, wind_direction="NW")
    filled = apply_weather([entry], [_observation()], overwrite=False)

    assert entry.air_temperature == 22.0
    assert entry.wind_force == 3
    assert entry.wind_direction == "NW"
    assert entry.atmospheric_pressure == 1014.5
    assert entry.cloud_cover == 1
    assert filled == 1
    assert entry.weather_source == "open-meteo"


def test_overwrite_replaces_existing_values():
    entry = _entry(air_temperature=22.0, wind_force=3, wind_direction="NW")
    filled = apply_weather([entry], [_observation()], overwrite=True)

    assert filled == 1
    assert entry.air_temperature == 24.5
    assert entry.wind_force == 4
    assert entry.wind_direction == "NE"
    assert entry.weather_source == "open-meteo"


def test_missing_observation_leaves_entry_untouched():
    covered = _entry()
    gap = _entry(wind_force=2)

    filled = apply_weather([covered, gap], [_observation(), None], overwrite=True)

    assert filled == 1
    assert gap.wind_force == 2
    assert gap.atmospheric_pressure is None
    assert gap.weather_source is None


def test_all_null_observation_is_not_counted():
    # Archive can return a row of nulls (e.g. date not yet in ERA5): the entry
    # must not be marked as enriched.
    entry = _entry()
    filled = apply_weather([entry], [WeatherObservation()], overwrite=False)

    assert filled == 0
    assert entry.weather_source is None
