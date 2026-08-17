"""Recurring annual window expansion (PRD: weather analysis §6)."""
from datetime import date

import pytest

from app.analysis.window import (
    WindowError,
    crosses_new_year,
    default_year_range,
    describe,
    expand_window,
)


def test_expands_one_window_per_year():
    windows = expand_window(10, 24, 10, 30, 2020, 2024)

    assert [w.year for w in windows] == [2020, 2021, 2022, 2023, 2024]
    assert windows[0].start == date(2020, 10, 24)
    assert windows[0].end == date(2020, 10, 30)
    # inclusive of both ends: 24th through 30th is seven days
    assert len(windows[0].dates) == 7
    assert windows[0].dates[0] == date(2020, 10, 24)
    assert windows[0].dates[-1] == date(2020, 10, 30)


def test_single_day_window():
    windows = expand_window(7, 1, 7, 1, 2023, 2023)
    assert len(windows) == 1
    assert windows[0].dates == [date(2023, 7, 1)]


def test_saturday_to_saturday_charter_week_is_eight_days():
    """The primary use case: a charter week runs Saturday to Saturday."""
    windows = expand_window(6, 20, 6, 27, 2026, 2026)
    assert len(windows[0].dates) == 8


def test_window_crossing_new_year_ends_in_following_year():
    windows = expand_window(12, 28, 1, 3, 2020, 2022)

    # the window is identified by the year containing its START date
    assert [w.year for w in windows] == [2020, 2021, 2022]
    assert windows[0].start == date(2020, 12, 28)
    assert windows[0].end == date(2021, 1, 3)
    assert len(windows[0].dates) == 7
    assert windows[0].dates[-1] == date(2021, 1, 3)


def test_crosses_new_year_detection():
    assert crosses_new_year(12, 28, 1, 3) is True
    assert crosses_new_year(10, 24, 10, 30) is False
    # same month/day is a single-day window, not a wrap
    assert crosses_new_year(7, 1, 7, 1) is False
    # earlier day in the same month wraps
    assert crosses_new_year(7, 10, 7, 2) is True


def test_leap_day_inside_window_is_skipped_in_common_years():
    """Feb 29 in the window interior needs no special handling: it simply
    does not occur in a common year, so the window is a day shorter."""
    windows = expand_window(2, 27, 3, 2, 2023, 2024)

    common, leap = windows
    assert common.dates == [date(2023, 2, 27), date(2023, 2, 28),
                            date(2023, 3, 1), date(2023, 3, 2)]
    assert date(2024, 2, 29) in leap.dates
    assert len(leap.dates) == 5


def test_leap_day_boundary_rejected_unless_every_year_is_leap():
    """The year range is inclusive, so any common year in it blocks a Feb 29
    boundary — naming only leap years is not enough if the span covers others."""
    with pytest.raises(WindowError, match="February 29"):
        expand_window(2, 29, 3, 5, 2023, 2024)

    # 2020-2024 spans 2021-2023, which are common years
    with pytest.raises(WindowError, match="2021, 2022, 2023"):
        expand_window(2, 29, 3, 5, 2020, 2024)

    # a range consisting only of leap years is allowed
    windows = expand_window(2, 29, 3, 5, 2020, 2020)
    assert windows[0].start == date(2020, 2, 29)
    assert windows[0].end == date(2020, 3, 5)


def test_leap_day_end_boundary_checks_the_year_it_falls_in():
    """A wrapping window's end lands in year+1, so that is the year whose
    leapness decides whether Feb 29 is reachable."""
    # starts Dec 2019/2023 -> ends Feb 2020/2024, both leap years
    windows = expand_window(12, 28, 2, 29, 2019, 2019)
    assert windows[0].end == date(2020, 2, 29)

    # starts Dec 2020 -> would end Feb 2021, not a leap year
    with pytest.raises(WindowError, match="February 29"):
        expand_window(12, 28, 2, 29, 2020, 2020)


def test_rejects_reversed_year_range():
    with pytest.raises(WindowError, match="no later than"):
        expand_window(10, 24, 10, 30, 2024, 2020)


def test_same_first_and_last_year_is_valid():
    windows = expand_window(10, 24, 10, 30, 2024, 2024)
    assert len(windows) == 1


@pytest.mark.parametrize("month,day", [
    (0, 15), (13, 1), (1, 0), (1, 32), (4, 31), (2, 30), (6, 31),
])
def test_rejects_impossible_month_day(month, day):
    with pytest.raises(WindowError, match="Invalid"):
        expand_window(month, day, 12, 31, 2020, 2024)
    with pytest.raises(WindowError, match="Invalid"):
        expand_window(1, 1, month, day, 2020, 2024)


def test_default_year_range_excludes_the_incomplete_current_year():
    assert default_year_range(today=date(2026, 8, 14)) == (2011, 2025)
    # even on the last day of a year, that year is not yet complete
    assert default_year_range(today=date(2026, 12, 31)) == (2011, 2025)
    assert default_year_range(today=date(2026, 8, 14), span=20) == (2006, 2025)


def test_describe_reads_as_a_date_span():
    assert describe(10, 24, 10, 30) == "24 Oct - 30 Oct"
    assert describe(12, 28, 1, 3) == "28 Dec - 3 Jan"
