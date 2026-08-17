"""Recurring annual date windows (PRD §6).

A window is a month/day span repeated across a run of historical years, e.g.
October 24-30 for 2010-2024. The window may cross New Year, in which case its
end falls in the following calendar year and the window is identified by the
year containing its *start* date.
"""
import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional


class WindowError(ValueError):
    """The requested window or year range cannot be analyzed."""


@dataclass(frozen=True)
class AnalysisWindow:
    """One year's concrete instance of the recurring window."""
    year: int              # year containing the start date; identifies the window
    start: date
    end: date

    @property
    def dates(self) -> list[date]:
        """Every local calendar day in the window, inclusive of both ends."""
        span = (self.end - self.start).days
        return [self.start + timedelta(days=n) for n in range(span + 1)]


def _valid_month_day(month: int, day: int) -> bool:
    """Month/day validity ignoring the year, so Feb 29 passes here.

    Whether Feb 29 survives depends on the selected years, which is decided in
    expand_window rather than here.
    """
    if not 1 <= month <= 12:
        return False
    # 2000 is a leap year, so its month lengths admit Feb 29.
    return 1 <= day <= calendar.monthrange(2000, month)[1]


def crosses_new_year(start_month: int, start_day: int,
                     end_month: int, end_day: int) -> bool:
    """True when the end month/day precedes the start, so the end is in year+1."""
    return (end_month, end_day) < (start_month, start_day)


def expand_window(
    start_month: int,
    start_day: int,
    end_month: int,
    end_day: int,
    first_year: int,
    last_year: int,
) -> list[AnalysisWindow]:
    """Concrete windows for each year in [first_year, last_year].

    Raises WindowError for an invalid month/day, a reversed year range, or a
    Feb 29 boundary that not every selected year supports.
    """
    if not _valid_month_day(start_month, start_day):
        raise WindowError(f"Invalid start date: {start_month:02d}-{start_day:02d}")
    if not _valid_month_day(end_month, end_day):
        raise WindowError(f"Invalid end date: {end_month:02d}-{end_day:02d}")
    if first_year > last_year:
        raise WindowError(
            f"First year {first_year} must be no later than last year {last_year}"
        )

    years = range(first_year, last_year + 1)
    wraps = crosses_new_year(start_month, start_day, end_month, end_day)

    # A Feb 29 *boundary* has no valid date in a common year, so the window
    # would silently shift. Reject unless every selected year is a leap year.
    # (A Feb 29 falling in the interior of a window is handled by dates(),
    # which simply skips it in common years.)
    for month, day, label in ((start_month, start_day, "start"),
                              (end_month, end_day, "end")):
        if (month, day) == (2, 29):
            # The end of a wrapping window lands in the year after its start.
            offset = 1 if (wraps and label == "end") else 0
            non_leap = [y + offset for y in years
                        if not calendar.isleap(y + offset)]
            if non_leap:
                raise WindowError(
                    f"February 29 is not a valid {label} date in "
                    f"{', '.join(str(y) for y in non_leap)}"
                )

    windows = []
    for year in years:
        start = date(year, start_month, start_day)
        end = date(year + 1 if wraps else year, end_month, end_day)
        windows.append(AnalysisWindow(year=year, start=start, end=end))
    return windows


def default_year_range(today: Optional[date] = None, span: int = 15) -> tuple[int, int]:
    """The last `span` complete calendar years (PRD §6).

    The current year is incomplete, so it is never preselected.
    """
    last = (today or date.today()).year - 1
    return last - span + 1, last


def describe(start_month: int, start_day: int,
             end_month: int, end_day: int) -> str:
    """Human label for a window, e.g. "24 Oct - 30 Oct"."""
    names = calendar.month_abbr
    return (f"{start_day} {names[start_month]} - "
            f"{end_day} {names[end_month]}")
