"""Daily aggregation, ratings, and lost days (PRD: weather analysis §8)."""
import json
import os
from datetime import date, datetime, timezone

import pytest

from app.analysis.aggregate import (
    Thresholds,
    aggregate_days,
    degrees_to_octant,
    is_lost_day,
    rate_day,
    wind_bin,
)
from app.analysis.provider import HourlyObservation, OpenMeteoArchiveProvider
from app.models import SailingRating

import httpx

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
T = Thresholds()


def _hour(local: str, wind=None, direction=None, gust=None, rain=None,
          pressure=None) -> HourlyObservation:
    """An observation at a naive local time; UTC is irrelevant to grouping."""
    ts = datetime.fromisoformat(local)
    return HourlyObservation(
        timestamp_utc=ts.replace(tzinfo=timezone.utc),
        local_timestamp=ts,
        wind_speed_kn=wind,
        wind_direction_deg=direction,
        wind_gust_kn=gust,
        precipitation_mm=rain,
        pressure_msl_hpa=pressure,
    )


# --- direction sectors ------------------------------------------------------

@pytest.mark.parametrize("degrees,expected", [
    (0, "N"), (360, "N"), (359, "N"), (22, "N"),
    (23, "NE"), (45, "NE"), (67, "NE"),
    (68, "E"), (90, "E"), (112, "E"),
    (135, "SE"), (180, "S"), (225, "SW"), (270, "W"), (315, "NW"),
    (337, "NW"), (338, "N"),        # wraps back to north
    (-45, "NW"),                    # negative degrees normalise
])
def test_degrees_to_octant(degrees, expected):
    assert degrees_to_octant(degrees) == expected


# --- wind bins --------------------------------------------------------------

@pytest.mark.parametrize("speed,expected", [
    (0, "<10"), (9.9, "<10"),
    (10, "10-15"), (14.9, "10-15"),
    (15, "15-20"), (19.9, "15-20"),
    (20, "20-25"), (24.9, "20-25"),
    (25, "25-30"), (29.9, "25-30"),
    (30, ">30"), (55, ">30"),
])
def test_wind_bin_boundaries(speed, expected):
    assert wind_bin(speed) == expected


# --- ratings ----------------------------------------------------------------

@pytest.mark.parametrize("wind,gust,rain,expected", [
    # green: below every yellow limit
    (0, 0, 0, SailingRating.green),
    (19.9, 30, 4.9, SailingRating.green),
    # PRD §8: "20 kn and 5 mm are Yellow"
    (20.0, 0, 0, SailingRating.yellow),
    (0, 0, 5.0, SailingRating.yellow),
    # PRD §8: "30 kn and 30 mm remain Yellow"
    (30.0, 0, 0, SailingRating.yellow),
    (0, 0, 30.0, SailingRating.yellow),
    (30.0, 40.0, 30.0, SailingRating.yellow),
    # red: strictly greater than a red limit
    (30.1, 0, 0, SailingRating.red),
    (0, 40.1, 0, SailingRating.red),
    (0, 0, 30.1, SailingRating.red),
    # red beats yellow regardless of the other values
    (35, 0, 0, SailingRating.red),
    (5, 0, 100, SailingRating.red),
])
def test_rating_boundaries(wind, gust, rain, expected):
    assert rate_day(wind, gust, rain, T) == expected


def test_gust_alone_can_make_a_day_red():
    """A calm-average day with a violent gust is still red."""
    assert rate_day(12.0, 45.0, 0.0, T) == SailingRating.red


def test_rating_is_none_without_measurements():
    assert rate_day(None, None, None, T) is None


def test_missing_gust_does_not_block_a_rating():
    assert rate_day(10.0, None, 0.0, T) == SailingRating.green


# --- lost days --------------------------------------------------------------

@pytest.mark.parametrize("wind,rain,expected", [
    (30.0, 20.0, False),    # both exactly at the limit: not lost
    (30.1, 0, True),
    (0, 20.1, True),
    (0, 0, False),
])
def test_lost_day_rule(wind, rain, expected):
    assert is_lost_day(wind, rain, T) is expected


def test_a_yellow_day_can_still_be_lost():
    """The lost-day rain limit (20 mm) is below the red one (30 mm), so a
    yellow day can cost a day's sailing."""
    assert rate_day(10.0, 0.0, 25.0, T) == SailingRating.yellow
    assert is_lost_day(10.0, 25.0, T) is True


# --- thresholds -------------------------------------------------------------

def test_thresholds_round_trip_through_a_dict():
    custom = Thresholds(yellow_wind_kn=18.0, red_rain_mm=25.0)
    assert Thresholds.from_dict(custom.as_dict()) == custom


def test_thresholds_default_when_absent():
    assert Thresholds.from_dict(None) == Thresholds()
    assert Thresholds.from_dict({}) == Thresholds()


def test_custom_thresholds_change_the_rating():
    strict = Thresholds(yellow_wind_kn=12.0)
    assert rate_day(15.0, 0, 0, T) == SailingRating.green
    assert rate_day(15.0, 0, 0, strict) == SailingRating.yellow


# --- daily aggregation ------------------------------------------------------

def test_aggregates_hours_into_one_local_day():
    hours = [
        _hour("2024-10-24T00:00", wind=10, direction=0,   gust=15, rain=1.0),
        _hour("2024-10-24T01:00", wind=20, direction=90,  gust=25, rain=2.0),
        _hour("2024-10-24T02:00", wind=30, direction=90,  gust=35, rain=0.5),
    ]

    day, = aggregate_days(hours, window_year=2024)

    assert day.local_date == date(2024, 10, 24)
    assert day.window_year == 2024
    assert day.avg_wind_kn == 20.0
    assert day.max_wind_kn == 30
    assert day.max_gust_kn == 35
    assert day.rain_total_mm == 3.5
    assert day.dominant_direction == "E"      # 90 deg twice
    assert day.hours == 3


def test_splits_hours_across_local_days():
    hours = [
        _hour("2024-10-24T23:00", wind=10, direction=0, rain=1.0),
        _hour("2024-10-25T00:00", wind=20, direction=0, rain=2.0),
    ]

    first, second = aggregate_days(hours, window_year=2024)

    assert (first.local_date, first.max_wind_kn) == (date(2024, 10, 24), 10)
    assert (second.local_date, second.max_wind_kn) == (date(2024, 10, 25), 20)


def test_days_come_back_in_calendar_order():
    hours = [_hour(f"2024-10-{d}T12:00", wind=5, direction=0)
             for d in (26, 24, 25)]

    days = aggregate_days(hours, window_year=2024)

    assert [d.local_date.day for d in days] == [24, 25, 26]


def test_ignores_missing_values_without_dropping_the_day():
    hours = [
        _hour("2024-10-24T00:00", wind=10, direction=0, rain=None),
        _hour("2024-10-24T01:00", wind=None, direction=None, rain=2.0),
    ]

    day, = aggregate_days(hours, window_year=2024)

    assert day.avg_wind_kn == 10          # the None hour is not averaged in
    assert day.rain_total_mm == 2.0
    assert day.hours == 1


def test_day_without_any_measurements_is_unrated():
    day, = aggregate_days([_hour("2024-10-24T00:00")], window_year=2024)

    assert day.sailing_rating is None
    assert day.avg_wind_kn is None
    assert day.lost_day is False


def test_dominant_direction_ties_resolve_deterministically():
    """Equal counts pick the sector nearest north, so repeated runs agree."""
    hours = [
        _hour("2024-10-24T00:00", wind=5, direction=270),   # W
        _hour("2024-10-24T01:00", wind=5, direction=90),    # E
    ]

    day, = aggregate_days(hours, window_year=2024)

    assert day.dominant_direction == "E"


def test_classification_is_applied_per_day():
    hours = [
        _hour("2024-10-24T00:00", wind=5,  direction=0, rain=0.0),
        _hour("2024-10-25T00:00", wind=35, direction=0, rain=0.0),
    ]

    green, red = aggregate_days(hours, window_year=2024)

    assert green.sailing_rating == SailingRating.green
    assert green.lost_day is False
    assert red.sailing_rating == SailingRating.red
    assert red.lost_day is True


# --- against recorded provider data ----------------------------------------

def _provider_days(fixture: str, start: date, end: date):
    with open(os.path.join(FIXTURES, fixture), encoding="utf-8") as f:
        payload = json.load(f)
    provider = OpenMeteoArchiveProvider(client=httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))
    ))
    result = provider.fetch(43.82, 15.60, "Europe/Zagreb", start, end)
    return aggregate_days(result.observations, window_year=start.year)


def test_aggregates_a_real_recorded_window():
    days = _provider_days("analysis_archive_murter.json",
                          date(2024, 10, 24), date(2024, 10, 30))

    assert len(days) == 7
    assert [d.local_date.day for d in days] == [24, 25, 26, 27, 28, 29, 30]
    for day in days:
        assert day.sailing_rating is not None
        assert day.dominant_direction in {
            "N", "NE", "E", "SE", "S", "SW", "W", "NW"}
        assert day.max_wind_kn >= day.avg_wind_kn


def test_dst_day_aggregates_all_25_hours():
    """PRD §8: the repeated local hour is retained, so the transition day has
    25 hourly samples feeding its mean."""
    days = _provider_days("analysis_archive_dst.json",
                          date(2024, 10, 26), date(2024, 10, 28))

    by_date = {d.local_date: d for d in days}
    assert by_date[date(2024, 10, 26)].hours == 24
    assert by_date[date(2024, 10, 27)].hours == 25
    assert by_date[date(2024, 10, 28)].hours == 24
