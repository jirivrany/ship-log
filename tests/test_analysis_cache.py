"""Hourly observation caching (PRD §7, AC7).

ERA5 is immutable, so a repeated request must be served entirely from SQLite.
"""
from datetime import date, datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis.cache import (
    cached_days,
    contiguous_runs,
    observations_for,
    store_days,
)
from app.analysis.provider import (
    HourlyObservation,
    ProviderError,
    ProviderResult,
)
from app.models import HourlyObservationCache

MURTER = (43.82, 15.60)
ZAGREB = "Europe/Zagreb"


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class FakeProvider:
    """Records every fetch so cache hits are provable by call count."""

    provider = "open-meteo"
    model = "era5"

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[date, date]] = []
        self.fail = fail

    def fetch(self, lat, lon, tz_name, start, end) -> ProviderResult:
        self.calls.append((start, end))
        if self.fail:
            raise ProviderError("provider is down")
        observations = []
        day = start
        while day <= end:
            for hour in (0, 12):
                ts = datetime(day.year, day.month, day.day, hour,
                              tzinfo=timezone.utc)
                observations.append(HourlyObservation(
                    timestamp_utc=ts,
                    local_timestamp=ts,
                    wind_speed_kn=10.0 + hour,
                    wind_direction_deg=90.0,
                    wind_gust_kn=20.0,
                    precipitation_mm=0.5,
                    pressure_msl_hpa=1013.0,
                ))
            day = date.fromordinal(day.toordinal() + 1)
        return ProviderResult(
            observations=observations,
            resolved_lat=43.75, resolved_lon=15.5,
            provider=self.provider, model=self.model,
            retrieved_at=datetime.now(timezone.utc),
        )


def _week(start_day: int = 24, n: int = 7) -> list[date]:
    return [date(2024, 10, start_day + i) for i in range(n)]


# --- contiguous runs --------------------------------------------------------

def test_contiguous_runs_collapse_into_spans():
    days = [date(2024, 10, d) for d in (24, 25, 26)]
    assert contiguous_runs(days) == [(date(2024, 10, 24), date(2024, 10, 26))]


def test_contiguous_runs_split_on_gaps():
    days = [date(2024, 10, d) for d in (24, 25, 28, 30)]
    assert contiguous_runs(days) == [
        (date(2024, 10, 24), date(2024, 10, 25)),
        (date(2024, 10, 28), date(2024, 10, 28)),
        (date(2024, 10, 30), date(2024, 10, 30)),
    ]


def test_contiguous_runs_handles_month_boundaries():
    days = [date(2024, 10, 31), date(2024, 11, 1)]
    assert contiguous_runs(days) == [(date(2024, 10, 31), date(2024, 11, 1))]


def test_contiguous_runs_of_nothing():
    assert contiguous_runs([]) == []


# --- fetching and caching ---------------------------------------------------

def test_first_run_fetches_once_for_a_contiguous_window(session):
    provider = FakeProvider()

    observations, resolved = observations_for(
        session, provider, "murtersko_more", *MURTER, ZAGREB, _week())

    assert provider.calls == [(date(2024, 10, 24), date(2024, 10, 30))]
    assert len(observations) == 14        # 7 days x 2 hours
    assert resolved == (43.75, 15.5)


def test_repeated_request_makes_no_provider_call(session):
    """AC7: the whole point of the cache."""
    first = FakeProvider()
    observations_for(session, first, "murtersko_more", *MURTER, ZAGREB, _week())

    second = FakeProvider()
    observations, resolved = observations_for(
        session, second, "murtersko_more", *MURTER, ZAGREB, _week())

    assert second.calls == []
    assert len(observations) == 14
    assert resolved == (43.75, 15.5)      # recovered from the cached rows


def test_only_missing_days_are_fetched(session):
    provider = FakeProvider()
    observations_for(session, provider, "murtersko_more", *MURTER, ZAGREB,
                     _week(24, 5))        # 24-28 cached

    provider.calls.clear()
    observations, _ = observations_for(
        session, provider, "murtersko_more", *MURTER, ZAGREB, _week(24, 7))

    # only 29-30 are missing, and they are contiguous -> one call
    assert provider.calls == [(date(2024, 10, 29), date(2024, 10, 30))]
    assert len(observations) == 14


def test_split_gaps_become_separate_calls(session):
    provider = FakeProvider()
    # cache 24 and 27 only
    observations_for(session, provider, "murtersko_more", *MURTER, ZAGREB,
                     [date(2024, 10, 24)])
    observations_for(session, provider, "murtersko_more", *MURTER, ZAGREB,
                     [date(2024, 10, 27)])
    provider.calls.clear()

    observations_for(session, provider, "murtersko_more", *MURTER, ZAGREB,
                     _week(24, 5))        # 24,25,26,27,28

    assert provider.calls == [
        (date(2024, 10, 25), date(2024, 10, 26)),
        (date(2024, 10, 28), date(2024, 10, 28)),
    ]


def test_cache_is_keyed_by_location(session):
    provider = FakeProvider()
    observations_for(session, provider, "murtersko_more", *MURTER, ZAGREB, _week())
    provider.calls.clear()

    observations_for(session, provider, "kornati", 43.78, 15.30, ZAGREB, _week())

    # a different location must not read another's rows
    assert provider.calls == [(date(2024, 10, 24), date(2024, 10, 30))]


def test_cache_is_keyed_by_provider_and_model(session):
    observations_for(session, FakeProvider(), "murtersko_more", *MURTER,
                     ZAGREB, _week())

    class Cerra(FakeProvider):
        provider = "copernicus"
        model = "cerra"

    other = Cerra()
    observations_for(session, other, "murtersko_more", *MURTER, ZAGREB, _week())

    # a future provider fills its own rows rather than colliding
    assert other.calls == [(date(2024, 10, 24), date(2024, 10, 30))]
    rows = session.exec(select(HourlyObservationCache)).all()
    assert {r.provider for r in rows} == {"open-meteo", "copernicus"}


def test_observations_come_back_in_chronological_order(session):
    provider = FakeProvider()

    observations, _ = observations_for(
        session, provider, "murtersko_more", *MURTER, ZAGREB, _week())

    stamps = [o.timestamp_utc for o in observations]
    assert stamps == sorted(stamps)


def test_values_survive_the_cache_round_trip(session):
    provider = FakeProvider()
    fresh, _ = observations_for(session, provider, "murtersko_more", *MURTER,
                                ZAGREB, [date(2024, 10, 24)])

    from_cache, _ = observations_for(session, FakeProvider(), "murtersko_more",
                                     *MURTER, ZAGREB, [date(2024, 10, 24)])

    assert fresh == from_cache


def test_provider_failure_propagates_and_caches_nothing(session):
    """PRD §7: a failure must not leave a successful-looking empty result."""
    with pytest.raises(ProviderError):
        observations_for(session, FakeProvider(fail=True), "murtersko_more",
                         *MURTER, ZAGREB, _week())

    assert session.exec(select(HourlyObservationCache)).all() == []


def test_days_the_provider_has_no_data_for_are_cached_as_empty(session):
    """An ERA5 gap is permanent, so caching it stops endless re-asking."""
    class Empty(FakeProvider):
        def fetch(self, lat, lon, tz_name, start, end):
            result = super().fetch(lat, lon, tz_name, start, end)
            return ProviderResult(
                observations=[], resolved_lat=result.resolved_lat,
                resolved_lon=result.resolved_lon, provider=self.provider,
                model=self.model, retrieved_at=result.retrieved_at,
            )

    provider = Empty()
    observations, _ = observations_for(session, provider, "murtersko_more",
                                       *MURTER, ZAGREB, _week())
    assert observations == []

    provider.calls.clear()
    observations_for(session, provider, "murtersko_more", *MURTER, ZAGREB, _week())
    assert provider.calls == []           # the empty days are not re-fetched


def test_no_days_requested_makes_no_call(session):
    provider = FakeProvider()
    observations, resolved = observations_for(
        session, provider, "murtersko_more", *MURTER, ZAGREB, [])

    assert (observations, resolved, provider.calls) == ([], None, [])


# --- storage primitives -----------------------------------------------------

def test_store_and_read_back_one_day(session):
    provider = FakeProvider()
    result = provider.fetch(*MURTER, ZAGREB, date(2024, 10, 24), date(2024, 10, 24))

    store_days(session, "open-meteo", "era5", "murtersko_more", result,
               [date(2024, 10, 24)])
    session.commit()

    rows = cached_days(session, "open-meteo", "era5", "murtersko_more",
                       [date(2024, 10, 24)])
    assert set(rows) == {date(2024, 10, 24)}
    assert rows[date(2024, 10, 24)].resolved_lat == 43.75


def test_storing_the_same_day_twice_updates_rather_than_duplicates(session):
    provider = FakeProvider()
    result = provider.fetch(*MURTER, ZAGREB, date(2024, 10, 24), date(2024, 10, 24))

    for _ in range(2):
        store_days(session, "open-meteo", "era5", "murtersko_more", result,
                   [date(2024, 10, 24)])
        session.commit()

    rows = session.exec(select(HourlyObservationCache)).all()
    assert len(rows) == 1
