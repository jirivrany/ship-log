"""Chart geometry for the result page (PRD: weather analysis §9.7)."""
from datetime import date

import pytest

from app.analysis.aggregate import DailyAggregate, Thresholds
from app.analysis.charts import (
    RATING_COLORS,
    rainfall_histogram,
    rating_bars,
    wind_histogram,
    wind_rose,
)
from app.analysis.statistics import summarize
from app.models import SailingRating

T = Thresholds()


def _day(day: int, *, wind=5.0, rain=0.0, direction="N",
         rating=SailingRating.green) -> DailyAggregate:
    return DailyAggregate(
        local_date=date(2024, 10, day), window_year=2024,
        avg_wind_kn=wind, max_wind_kn=wind, max_gust_kn=wind + 5,
        rain_total_mm=rain, dominant_direction=direction,
        sailing_rating=rating, lost_day=False, hours=24,
    )


# --- wind rose --------------------------------------------------------------

def test_wind_rose_has_all_eight_sectors_in_compass_order():
    summary = summarize([_day(1, direction="N")], T)

    rose = wind_rose(summary["direction_distribution"])

    assert [s.sector for s in rose] == ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def test_petal_radius_scales_with_frequency():
    days = [_day(i + 1, direction="N") for i in range(4)]
    days += [_day(10, direction="E")]        # a quarter as many

    rose = {s.sector: s for s in wind_rose(summarize(days, T)["direction_distribution"])}

    assert rose["N"].days == 4
    assert rose["E"].days == 1
    # the busiest sector reaches full radius, the others are shorter paths
    assert rose["N"].path and rose["E"].path
    assert len(rose["N"].path) > 0
    assert rose["S"].path == ""              # unused sectors draw nothing


def test_unused_sectors_are_present_but_empty():
    rose = {s.sector: s for s in
            wind_rose(summarize([_day(1, direction="N")], T)["direction_distribution"])}

    assert rose["S"].days == 0
    assert rose["S"].percent == 0.0
    assert rose["S"].path == ""
    # every sector still gets a label position so the compass stays complete
    assert rose["S"].label_x and rose["S"].label_y


def test_north_petal_points_up():
    """SVG y grows downward, so north must sit above the centre."""
    rose = {s.sector: s for s in
            wind_rose(summarize([_day(1, direction="N")], T)["direction_distribution"],
                      size=220)}

    assert rose["N"].label_y < 110           # above the centre
    assert rose["S"].label_y > 110           # below it
    assert rose["E"].label_x > 110           # right of it
    assert rose["W"].label_x < 110


def test_wind_rose_of_nothing_draws_no_petals():
    rose = wind_rose(summarize([], T)["direction_distribution"])

    assert len(rose) == 8
    assert all(s.path == "" for s in rose)


# --- histograms -------------------------------------------------------------

def test_wind_histogram_covers_every_bin():
    days = [_day(1, wind=5.0), _day(2, wind=12.0), _day(3, wind=12.0)]

    bars = wind_histogram(summarize(days, T))

    assert [b.label for b in bars] == ["<10", "10-15", "15-20",
                                       "20-25", "25-30", ">30"]
    by_label = {b.label: b for b in bars}
    assert by_label["10-15"].days == 2
    assert by_label["10-15"].width_pct == 100.0    # the tallest bar
    assert by_label["<10"].width_pct == 50.0       # half as tall
    assert by_label["20-25"].width_pct == 0.0


def test_rating_bars_carry_their_colours():
    days = [_day(1, rating=SailingRating.green),
            _day(2, rating=SailingRating.red)]

    bars = {b.label: b for b in rating_bars(summarize(days, T))}

    assert bars["green"].color == RATING_COLORS["green"]
    assert bars["red"].color == RATING_COLORS["red"]
    assert bars["green"].percent == 50.0


def test_rainfall_histogram_separates_dry_days():
    days = [
        _day(1, rain=0.0), _day(2, rain=0.0),   # dry
        _day(3, rain=0.5),                       # <1
        _day(4, rain=3.0),                       # 1-5
        _day(5, rain=10.0),                      # 5-20
        _day(6, rain=25.0),                      # >20
    ]

    bars = {b.label: b for b in rainfall_histogram(days)}

    assert bars["dry"].days == 2
    assert bars["<1"].days == 1
    assert bars["1-5"].days == 1
    assert bars["5-20"].days == 1
    assert bars[">20"].days == 1


def test_rainfall_histogram_bin_boundaries():
    """5-20 is inclusive at 20 mm; anything above starts the >20 bucket."""
    bars = {b.label: b for b in
            rainfall_histogram([_day(1, rain=20.0), _day(2, rain=20.1)])}

    assert bars["5-20"].days == 1
    assert bars[">20"].days == 1


def test_rainfall_histogram_ignores_days_without_measurements():
    days = [_day(1, rain=None), _day(2, rain=0.0)]

    bars = {b.label: b for b in rainfall_histogram(days)}

    assert bars["dry"].days == 1
    assert bars["dry"].percent == 100.0


def test_histograms_of_nothing_do_not_divide_by_zero():
    assert all(b.width_pct == 0.0 for b in wind_histogram(summarize([], T)))
    assert all(b.width_pct == 0.0 for b in rainfall_histogram([]))
    assert all(b.width_pct == 0.0 for b in rating_bars(summarize([], T)))
