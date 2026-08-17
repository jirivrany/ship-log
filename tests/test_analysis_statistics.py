"""Per-location statistics and comparison (PRD: weather analysis §8, §9, §10)."""
from datetime import date

import pytest

from app.analysis.aggregate import DailyAggregate, Thresholds
from app.analysis.statistics import (
    comparison_statement,
    lost_day_statistics,
    rainfall_statistics,
    rank,
    summarize,
    summarize_location,
    wind_categories,
)
from app.models import SailingRating

T = Thresholds()


def _day(day: int, *, year=2024, wind=5.0, gust=None, rain=0.0,
         direction="N", rating=SailingRating.green, lost=False,
         month=10) -> DailyAggregate:
    return DailyAggregate(
        local_date=date(year, month, day),
        window_year=year,
        avg_wind_kn=wind,
        max_wind_kn=wind,
        max_gust_kn=gust,
        rain_total_mm=rain,
        dominant_direction=direction,
        sailing_rating=rating,
        lost_day=lost,
        hours=24,
    )


# --- wind categories --------------------------------------------------------

def test_wind_categories_split_at_the_threshold_boundaries():
    days = [
        _day(1, wind=5.0),     # light: below 10
        _day(2, wind=9.9),     # light
        _day(3, wind=10.0),    # good sailing: 10 up to the yellow limit
        _day(4, wind=19.9),    # good sailing
        _day(5, wind=20.0),    # strong: at the yellow limit
        _day(6, wind=30.0),    # strong: still at the red limit
        _day(7, wind=30.1),    # dangerous: past the red limit
    ]

    categories = wind_categories(days, T)

    assert categories["light"]["days"] == 2
    assert categories["good_sailing"]["days"] == 2
    assert categories["strong"]["days"] == 2
    assert categories["dangerous"]["days"] == 1
    assert categories["light"]["percent"] == pytest.approx(28.6, abs=0.1)


def test_wind_categories_ignore_days_without_wind():
    days = [_day(1, wind=5.0), DailyAggregate(
        local_date=date(2024, 10, 2), window_year=2024, avg_wind_kn=None,
        max_wind_kn=None, max_gust_kn=None, rain_total_mm=None,
        dominant_direction=None, sailing_rating=None, lost_day=False, hours=0)]

    categories = wind_categories(days, T)

    assert categories["light"]["days"] == 1
    assert categories["light"]["percent"] == 100.0


# --- rainfall ---------------------------------------------------------------

def test_rainfall_counts_rainy_and_heavy_days():
    days = [
        _day(1, rain=0.0),
        _day(2, rain=4.9),     # below the rainy limit
        _day(3, rain=5.0),     # rainy (inclusive)
        _day(4, rain=30.0),    # rainy, not yet heavy
        _day(5, rain=30.1),    # heavy
    ]

    stats = rainfall_statistics(days, T)

    assert stats["rainy_days"] == 3
    assert stats["heavy_rain_days"] == 1
    assert stats["max_daily_rain_mm"] == 30.1


def test_consecutive_rain_events_need_at_least_two_days():
    days = [
        _day(1, rain=10.0), _day(2, rain=10.0),   # a 2-day event
        _day(3, rain=0.0),
        _day(4, rain=10.0),                        # isolated, not an event
        _day(5, rain=0.0),
        _day(6, rain=8.0), _day(7, rain=8.0), _day(8, rain=8.0),  # a 3-day event
    ]

    stats = rainfall_statistics(days, T)

    assert stats["consecutive_rain_events"] == 2
    assert stats["longest_consecutive_rain_days"] == 3


def test_consecutive_rain_does_not_straddle_two_windows():
    """The last day of one window and the first of the next are a year apart."""
    days = [_day(30, year=2023, rain=10.0), _day(24, year=2024, rain=10.0)]

    stats = rainfall_statistics(days, T)

    assert stats["consecutive_rain_events"] == 0


def test_rain_run_ending_at_the_window_edge_is_counted():
    days = [_day(29, rain=10.0), _day(30, rain=10.0)]

    assert rainfall_statistics(days, T)["consecutive_rain_events"] == 1


# --- lost days --------------------------------------------------------------

def test_lost_day_statistics_per_window():
    days = [
        _day(24, year=2022, lost=True), _day(25, year=2022, lost=True),
        _day(24, year=2023, lost=False),
        _day(24, year=2024, lost=True),
    ]

    stats = lost_day_statistics(days)

    assert stats["per_window"] == {"2022": 2, "2023": 0, "2024": 1}
    assert stats["total_lost_days"] == 3
    assert stats["windows_analyzed"] == 3
    assert stats["windows_with_lost_day"] == 2
    assert stats["mean_lost_days_per_window"] == 1.0
    assert stats["probability_window_has_lost_day"] == pytest.approx(66.7, abs=0.1)


def test_lost_day_probability_is_zero_without_lost_days():
    stats = lost_day_statistics([_day(24), _day(25)])
    assert stats["probability_window_has_lost_day"] == 0.0


def test_lost_day_statistics_of_nothing():
    stats = lost_day_statistics([])
    assert stats["windows_analyzed"] == 0
    assert stats["mean_lost_days_per_window"] == 0.0
    assert stats["probability_window_has_lost_day"] == 0.0


# --- the §10 summary shape --------------------------------------------------

def test_summary_has_exactly_the_documented_export_keys():
    summary = summarize([_day(24)], T)

    assert {"wind_bins", "direction_distribution", "rainfall_statistics",
            "sailing_categories", "lost_day_statistics"} <= summary.keys()


def test_wind_bins_cover_every_documented_bin():
    summary = summarize([_day(24, wind=5.0), _day(25, wind=22.0)], T)

    assert list(summary["wind_bins"]) == [
        "<10", "10-15", "15-20", "20-25", "25-30", ">30"]
    assert summary["wind_bins"]["<10"]["days"] == 1
    assert summary["wind_bins"]["20-25"]["days"] == 1
    assert summary["wind_bins"]["10-15"]["days"] == 0


def test_direction_distribution_covers_all_eight_sectors():
    days = [_day(24, direction="N"), _day(25, direction="SE"),
            _day(26, direction="SE")]

    summary = summarize(days, T)

    distribution = summary["direction_distribution"]
    assert list(distribution) == ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    assert distribution["SE"]["days"] == 2
    assert distribution["SE"]["percent"] == pytest.approx(66.7, abs=0.1)
    assert distribution["W"]["days"] == 0


def test_rating_percentages_sum_to_a_hundred():
    days = [_day(24, rating=SailingRating.green),
            _day(25, rating=SailingRating.yellow),
            _day(26, rating=SailingRating.red),
            _day(27, rating=SailingRating.green)]

    ratings = summarize(days, T)["sailing_categories"]["ratings"]

    assert ratings["green"]["days"] == 2
    assert ratings["green"]["percent"] == 50.0
    assert sum(r["percent"] for r in ratings.values()) == pytest.approx(100.0)


def test_summary_of_no_days_does_not_divide_by_zero():
    summary = summarize([], T)

    assert summary["days_analyzed"] == 0
    assert summary["wind_bins"]["<10"]["percent"] == 0.0
    assert summary["lost_day_statistics"]["windows_analyzed"] == 0


# --- per-location summaries and ranking ------------------------------------

def _summary(key, name, green_days, red_days, *, year=2024, lost=0):
    days = [_day(i + 1, year=year, rating=SailingRating.green)
            for i in range(green_days)]
    days += [_day(green_days + i + 1, year=year, wind=35.0,
                  rating=SailingRating.red, lost=i < lost)
             for i in range(red_days)]
    return summarize_location(key, name, "Adriatic", days, T)


def test_location_summary_reports_headline_numbers():
    summary = _summary("hvar", "Hvar", green_days=6, red_days=2, lost=2)

    assert summary.days == 8
    assert summary.green_pct == 75.0
    assert summary.red_pct == 25.0
    assert summary.windows == 1
    assert summary.lost_day_probability == 100.0
    assert summary.median_max_wind_kn is not None


def test_ranking_puts_the_greenest_location_first():
    hvar = _summary("hvar", "Hvar", 7, 1)
    rugen = _summary("rugen", "Rugen", 3, 5)
    split = _summary("split", "Split", 5, 3)

    assert [s.name for s in rank([rugen, split, hvar])] == ["Hvar", "Split", "Rugen"]


def test_ranking_breaks_ties_on_lost_day_risk():
    safe = _summary("a", "Alpha", 6, 2, lost=0)
    risky = _summary("b", "Bravo", 6, 2, lost=2)

    assert [s.name for s in rank([risky, safe])] == ["Alpha", "Bravo"]


def test_comparison_statement_names_best_and_worst_without_forecasting():
    hvar = _summary("hvar", "Hvar", 7, 1)
    rugen = _summary("rugen", "Rugen", 2, 6)

    statement = comparison_statement([rugen, hvar])

    assert "Hvar" in statement and "Rugen" in statement
    assert "highest share of green days" in statement
    # PRD §9: qualified, never presented as a forecast
    assert "not a forecast" in statement


def test_comparison_statement_for_a_single_location():
    statement = comparison_statement([_summary("hvar", "Hvar", 7, 1)])

    assert "Hvar" in statement
    assert "highest share" not in statement    # nothing to compare against
    assert "not a forecast" in statement


def test_comparison_statement_without_data_is_none():
    assert comparison_statement([]) is None
    assert comparison_statement([summarize_location("x", "X", "R", [], T)]) is None
