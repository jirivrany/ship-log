"""Write policy of the leg forecast applier.

Owns the rules that guard the skipper's own forecast notes: fill-empty by
default, explicit overwrite, provenance marking (mirrors weather_apply).
"""
from app.forecast import LegForecast
from app.forecast_apply import apply_forecast
from app.models import Leg


def _leg(**overrides) -> Leg:
    defaults = dict(voyage_id=1, from_port="Sukošan", to_port="Ždrelac",
                    date="2026-07-08", timezone="Europe/Zagreb")
    defaults.update(overrides)
    return Leg(**defaults)


def _forecast(**overrides) -> LegForecast:
    defaults = dict(sunrise="05:25", sunset="20:42",
                    forecast="Morning E 2 Bf, afternoon SW 4-5 Bf")
    defaults.update(overrides)
    return LegForecast(**defaults)


def test_fills_empty_fields_and_marks_source():
    leg = _leg()

    assert apply_forecast(leg, _forecast(), overwrite=False) is True

    assert leg.sunrise == "05:25"
    assert leg.sunset == "20:42"
    assert leg.forecast == "Morning E 2 Bf, afternoon SW 4-5 Bf"
    assert leg.forecast_source == "open-meteo"


def test_existing_text_is_protected_without_overwrite():
    leg = _leg(forecast="VHF: jugo backing to bora in the evening", sunset="20:40")

    wrote = apply_forecast(leg, _forecast(), overwrite=False)

    # the empty field still fills — that's a write, marked as such
    assert wrote is True
    assert leg.sunrise == "05:25"
    # the skipper's own values survive
    assert leg.forecast == "VHF: jugo backing to bora in the evening"
    assert leg.sunset == "20:40"


def test_overwrite_replaces_existing_values():
    leg = _leg(forecast="stale text", sunrise="05:00")

    apply_forecast(leg, _forecast(), overwrite=True)

    assert leg.forecast == "Morning E 2 Bf, afternoon SW 4-5 Bf"
    assert leg.sunrise == "05:25"


def test_nothing_written_when_all_fields_taken():
    leg = _leg(sunrise="05:00", sunset="20:40", forecast="my own words")

    assert apply_forecast(leg, _forecast(), overwrite=False) is False
    assert leg.forecast_source is None


def test_none_values_never_written():
    leg = _leg(sunset="20:40")

    wrote = apply_forecast(leg, LegForecast(sunrise=None, sunset=None, forecast=None),
                           overwrite=True)

    assert wrote is False
    assert leg.sunset == "20:40"
    assert leg.sunrise is None
    assert leg.forecast_source is None


def test_warnings_and_synoptic_are_never_touched():
    # those two fields have no auto source in this iteration — manual only
    leg = _leg(warnings="Bora warning N Adriatic", synoptic_situation="High over Azores")

    apply_forecast(leg, _forecast(), overwrite=True)

    assert leg.warnings == "Bora warning N Adriatic"
    assert leg.synoptic_situation == "High over Azores"
