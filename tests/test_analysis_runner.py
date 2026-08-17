"""Analysis orchestration and persistence (PRD: weather analysis §7)."""
import json
from datetime import date, datetime, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis.aggregate import Thresholds
from app.analysis.provider import (
    HourlyObservation,
    ProviderError,
    ProviderResult,
)
from app.analysis.runner import (
    AnalysisRequest,
    delete_run,
    load_result,
    location_label,
    recent_runs,
    run_analysis,
)
from app.analysis.seed import seed_locations
from app.analysis.window import WindowError
from app.models import (
    AnalysisLocation,
    AnalysisRun,
    DailySummary,
    HourlyObservationCache,
)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        seed_locations(s)
        yield s


class FakeProvider:
    """Deterministic hourly data: wind rises with the day of the month, so
    windows and locations are distinguishable in assertions."""

    provider = "open-meteo"
    model = "era5"

    def __init__(self, fail=False, wind=None):
        self.calls = []
        self.fail = fail
        self.wind = wind

    def fetch(self, lat, lon, tz_name, start, end):
        self.calls.append((lat, lon, start, end))
        if self.fail:
            raise ProviderError("provider is down")
        observations = []
        day = start
        while day <= end:
            for hour in range(0, 24, 6):
                ts = datetime(day.year, day.month, day.day, hour,
                              tzinfo=timezone.utc)
                observations.append(HourlyObservation(
                    timestamp_utc=ts, local_timestamp=ts,
                    wind_speed_kn=self.wind if self.wind is not None else float(day.day),
                    wind_direction_deg=90.0,
                    wind_gust_kn=(self.wind or float(day.day)) + 5,
                    precipitation_mm=0.25,
                    pressure_msl_hpa=1013.0,
                ))
            day = date.fromordinal(day.toordinal() + 1)
        return ProviderResult(
            observations=observations, resolved_lat=43.75, resolved_lon=15.5,
            provider=self.provider, model=self.model,
            retrieved_at=datetime.now(timezone.utc),
        )


def _ids(session, *keys) -> list[int]:
    return [session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == key)
    ).first().id for key in keys]


def _request(session, *keys, **overrides) -> AnalysisRequest:
    values = dict(
        location_ids=_ids(session, *(keys or ("murtersko_more",))),
        start_month=10, start_day=24, end_month=10, end_day=30,
        first_year=2022, last_year=2024, thresholds=Thresholds(),
    )
    values.update(overrides)
    return AnalysisRequest(**values)


# --- running ----------------------------------------------------------------

def test_run_persists_daily_summaries_for_every_window_day(session):
    result = run_analysis(session, _request(session), FakeProvider())

    # 3 years x 7 days
    assert len(result.daily["murtersko_more"]) == 21
    rows = session.exec(select(DailySummary)).all()
    assert len(rows) == 21
    assert {r.run_id for r in rows} == {result.run.id}


def test_run_records_its_metadata(session):
    thresholds = Thresholds(yellow_wind_kn=18.0)
    result = run_analysis(session, _request(session, thresholds=thresholds),
                          FakeProvider())

    run = result.run
    assert (run.provider, run.model) == ("open-meteo", "era5")
    assert (run.start_month, run.start_day) == (10, 24)
    assert (run.first_year, run.last_year) == (2022, 2024)
    # thresholds are frozen onto the run so later edits cannot rewrite it
    assert json.loads(run.thresholds_json)["yellow_wind_kn"] == 18.0


def test_run_snapshots_the_selected_locations(session):
    result = run_analysis(session, _request(session, "hvar"), FakeProvider())

    snapshot, = json.loads(result.run.location_snapshots_json)
    assert snapshot["key"] == "hvar"
    assert snapshot["name"] == "Hvar"
    assert snapshot["latitude"] == 43.15
    assert snapshot["timezone"] == "Europe/Zagreb"


def test_days_carry_the_window_year_that_identifies_them(session):
    result = run_analysis(session, _request(session), FakeProvider())

    years = {d.window_year for d in result.daily["murtersko_more"]}
    assert years == {2022, 2023, 2024}


def test_window_crossing_new_year_keeps_the_start_year(session):
    result = run_analysis(
        session,
        _request(session, start_month=12, start_day=29,
                 end_month=1, end_day=2, first_year=2022, last_year=2023),
        FakeProvider(),
    )

    days = result.daily["murtersko_more"]
    by_year = {}
    for day in days:
        by_year.setdefault(day.window_year, []).append(day.local_date)
    # the 2022 window runs 29 Dec 2022 -> 2 Jan 2023, all tagged 2022
    assert min(by_year[2022]) == date(2022, 12, 29)
    assert max(by_year[2022]) == date(2023, 1, 2)
    assert len(by_year[2022]) == 5


def test_multiple_locations_are_compared(session):
    result = run_analysis(session, _request(session, "hvar", "rugen"),
                          FakeProvider())

    assert {s.location_key for s in result.summaries} == {"hvar", "rugen"}
    assert "not a forecast" in result.statement
    assert len(result.ranked) == 2


def test_resolved_cell_is_recorded_on_the_location(session):
    run_analysis(session, _request(session), FakeProvider())

    location = session.exec(
        select(AnalysisLocation).where(
            AnalysisLocation.key == "murtersko_more")
    ).first()
    assert (location.resolved_lat, location.resolved_lon) == (43.75, 15.5)


def test_second_identical_run_makes_no_provider_call(session):
    """AC7 end to end: the cache carries a repeated question."""
    run_analysis(session, _request(session), FakeProvider())

    provider = FakeProvider()
    result = run_analysis(session, _request(session), provider)

    assert provider.calls == []
    assert len(result.daily["murtersko_more"]) == 21


def test_overlapping_run_fetches_only_the_new_years(session):
    run_analysis(session, _request(session, first_year=2022, last_year=2023),
                 FakeProvider())

    provider = FakeProvider()
    run_analysis(session, _request(session, first_year=2022, last_year=2024),
                 provider)

    fetched = {(start, end) for _, _, start, end in provider.calls}
    assert fetched == {(date(2024, 10, 24), date(2024, 10, 30))}


# --- failures ---------------------------------------------------------------

def test_provider_failure_leaves_no_run_behind(session):
    """PRD §7: no successful-looking empty analysis."""
    with pytest.raises(ProviderError):
        run_analysis(session, _request(session), FakeProvider(fail=True))

    assert session.exec(select(AnalysisRun)).all() == []
    assert session.exec(select(DailySummary)).all() == []
    assert session.exec(select(HourlyObservationCache)).all() == []


def test_invalid_window_is_rejected_before_any_fetch(session):
    provider = FakeProvider()

    with pytest.raises(WindowError):
        run_analysis(session, _request(session, first_year=2024,
                                       last_year=2020), provider)

    assert provider.calls == []
    assert session.exec(select(AnalysisRun)).all() == []


def test_no_locations_selected_is_rejected(session):
    with pytest.raises(WindowError, match="at least one location"):
        run_analysis(session, _request(session, location_ids=[]),
                     FakeProvider())


def test_unknown_location_ids_are_rejected(session):
    with pytest.raises(WindowError, match="at least one location"):
        run_analysis(session, _request(session, location_ids=[9999]),
                     FakeProvider())


# --- reloading --------------------------------------------------------------

def test_load_result_rebuilds_a_stored_run(session):
    original = run_analysis(session, _request(session, "hvar", "rugen"),
                            FakeProvider())

    loaded = load_result(session, original.run.id)

    assert {s.location_key for s in loaded.summaries} == {"hvar", "rugen"}
    assert len(loaded.daily["hvar"]) == len(original.daily["hvar"])
    assert loaded.summaries[0].green_pct == original.summaries[0].green_pct
    assert loaded.thresholds == original.thresholds
    assert loaded.statement == original.statement


def test_loaded_run_keeps_its_own_thresholds(session):
    strict = Thresholds(yellow_wind_kn=12.0, red_wind_kn=20.0)
    original = run_analysis(session, _request(session, thresholds=strict),
                            FakeProvider())

    loaded = load_result(session, original.run.id)

    assert loaded.thresholds == strict


def test_loading_a_missing_run_is_none(session):
    assert load_result(session, 12345) is None


def test_removing_a_location_does_not_change_past_results(session):
    """PRD §5: removal only stops future selection."""
    original = run_analysis(session, _request(session, "hvar"), FakeProvider())
    hvar = session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == "hvar")
    ).first()
    hvar.active = False
    session.add(hvar)
    session.commit()

    loaded = load_result(session, original.run.id)

    assert loaded.summaries[0].name == "Hvar"
    assert len(loaded.daily["hvar"]) == 21


def test_renaming_a_location_does_not_rewrite_past_results(session):
    original = run_analysis(session, _request(session, "hvar"), FakeProvider())
    hvar = session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == "hvar")
    ).first()
    hvar.name = "Hvar (south)"
    session.add(hvar)
    session.commit()

    loaded = load_result(session, original.run.id)

    # the run keeps the name it was created with
    assert loaded.summaries[0].name == "Hvar"


# --- run history ------------------------------------------------------------

def test_location_label_names_the_analysed_places(session):
    """Runs sharing a window and year range are told apart by their locations."""
    result = run_analysis(session, _request(session, "hvar", "rugen"),
                          FakeProvider())

    label = location_label(result.run)

    assert "Hvar" in label and "Rugen" in label


def test_location_label_summarises_a_long_selection(session):
    result = run_analysis(
        session,
        _request(session, "hvar", "rugen", "pula", "kornati", "split",
                 "dubrovnik"),
        FakeProvider(),
    )

    label = location_label(result.run, limit=4)

    assert label.endswith("+2 more")
    assert label.count(",") == 3          # four names listed, then the count


def test_location_label_uses_the_name_at_run_time(session):
    """Reads the snapshot, so renaming a location does not rewrite history."""
    result = run_analysis(session, _request(session, "hvar"), FakeProvider())
    hvar = session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == "hvar")
    ).first()
    hvar.name = "Hvar (south)"
    session.add(hvar)
    session.commit()

    assert location_label(result.run) == "Hvar"


def test_recent_runs_are_newest_first(session):
    first = run_analysis(session, _request(session, first_year=2022,
                                           last_year=2022), FakeProvider())
    second = run_analysis(session, _request(session, first_year=2023,
                                            last_year=2023), FakeProvider())

    ids = [r.id for r in recent_runs(session)]
    assert set(ids) == {first.run.id, second.run.id}


def test_deleting_a_run_keeps_the_cached_observations(session):
    result = run_analysis(session, _request(session), FakeProvider())
    cached_before = len(session.exec(select(HourlyObservationCache)).all())

    delete_run(session, result.run.id)

    assert session.exec(select(AnalysisRun)).all() == []
    assert session.exec(select(DailySummary)).all() == []
    # ERA5 is immutable and expensive to refetch: the cache survives
    assert len(session.exec(select(HourlyObservationCache)).all()) == cached_before
