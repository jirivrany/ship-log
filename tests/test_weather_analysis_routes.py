"""Weather Analysis page, location CRUD, and exports (PRD §9, AC1-AC9)."""
import re
from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis.provider import HourlyObservation, ProviderError, ProviderResult
from app.analysis.seed import seed_locations
from app.database import get_session
from app.main import app
from app.models import AnalysisLocation, AnalysisRun, DailySummary


class FakeProvider:
    provider = "open-meteo"
    model = "era5"

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def fetch(self, lat, lon, tz_name, start, end):
        self.calls.append((lat, lon, start, end))
        if self.fail:
            raise ProviderError("Weather provider request failed")
        observations = []
        day = start
        while day <= end:
            for hour in (0, 12):
                ts = datetime(day.year, day.month, day.day, hour,
                              tzinfo=timezone.utc)
                observations.append(HourlyObservation(
                    timestamp_utc=ts, local_timestamp=ts,
                    wind_speed_kn=12.0 + hour / 6, wind_direction_deg=90.0,
                    wind_gust_kn=20.0, precipitation_mm=0.4,
                    pressure_msl_hpa=1013.0,
                ))
            day = date.fromordinal(day.toordinal() + 1)
        return ProviderResult(
            observations=observations, resolved_lat=43.75, resolved_lon=15.5,
            provider=self.provider, model=self.model,
            retrieved_at=datetime.now(timezone.utc),
        )


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_locations(session)
        yield session


@pytest.fixture()
def provider():
    return FakeProvider()


@pytest.fixture()
def client(db_session, provider):
    app.dependency_overrides[get_session] = lambda: db_session
    with patch("app.main.init_db"), patch("app.main.os.makedirs"), \
         patch("app.routers.weather_analysis.run_analysis",
               side_effect=_runner(db_session, provider)):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    app.dependency_overrides.clear()


def _runner(session, provider):
    """Route the real runner through the fake provider."""
    from app.analysis.runner import run_analysis as real

    def run(passed_session, request, _provider=None):
        return real(passed_session, request, provider)
    return run


def _location_id(session, key: str) -> int:
    return session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == key)
    ).first().id


def _run(client, session, *keys, **overrides):
    data = {
        "location_ids": [str(_location_id(session, k)) for k in
                         (keys or ("murtersko_more",))],
        "start_day": "24", "start_month": "10",
        "end_day": "26", "end_month": "10",
        "first_year": "2023", "last_year": "2024",
    }
    data.update(overrides)
    return client.post("/weather-analysis/run", data=data, follow_redirects=False)


# --- the page ---------------------------------------------------------------

def test_page_is_reachable_and_independent_of_voyages(client):
    """AC1: a top-level page with no voyage selector."""
    response = client.get("/weather-analysis")

    assert response.status_code == 200
    assert "Weather Analysis" in response.text
    assert "/voyages/" not in response.text


def test_navigation_links_to_the_page(client):
    assert 'href="/weather-analysis"' in client.get("/").text


def test_form_offers_the_first_scenario_and_complete_years(client):
    """PRD §6: October 24-30 by default; the current year is not preselected."""
    html = client.get("/weather-analysis").text

    assert re.search(r'name="start_day"[^>]*value="24"', html)
    assert re.search(r'name="end_day"[^>]*value="30"', html)
    # October is preselected in both month dropdowns
    assert html.count('<option value="10" selected>Oct</option>') == 2

    last_complete = date.today().year - 1
    assert re.search(rf'name="first_year"[^>]*value="{last_complete - 14}"', html)
    assert re.search(rf'name="last_year"[^>]*value="{last_complete}"', html)
    # the incomplete current year is never the default
    assert not re.search(rf'name="last_year"[^>]*value="{date.today().year}"', html)


def test_page_shows_locations_grouped_by_region(client):
    html = client.get("/weather-analysis").text

    assert "Northern Adriatic" in html
    assert "Middle Adriatic" in html
    assert "Murtersko More" in html


def test_page_carries_the_attribution(client):
    """AC9."""
    html = client.get("/weather-analysis").text

    assert "Open-Meteo" in html and "ERA5" in html
    assert "not observations" in html


def test_form_can_show_that_an_analysis_is_running(client):
    """A cold run holds the POST for tens of seconds, so the submit must be
    able to report progress rather than looking like a dead button."""
    html = client.get("/weather-analysis").text

    assert 'id="analysis-form"' in html
    assert 'id="analyze-spinner"' in html
    assert 'id="analyze-status"' in html
    # the enhancing script is loaded on the form page, not just the result page
    assert "/static/js/weather_analysis.js" in html


def test_analyze_button_works_without_javascript(client, db_session):
    """The busy state is an enhancement: the form is a plain POST and the
    button must stay a real submit, never a disabled one."""
    html = client.get("/weather-analysis").text
    form = html.split('id="analysis-form"', 1)[1].split("</form>", 1)[0]

    assert 'action="/weather-analysis/run"' in html
    assert 'type="submit"' in form
    assert "disabled" not in form.split('id="analyze-button"', 1)[1][:200]
    # and submitting it still produces a run
    assert _run(client, db_session).status_code == 303


# --- running an analysis ----------------------------------------------------

def test_run_redirects_to_its_result_page(client, db_session):
    response = _run(client, db_session)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/weather-analysis/runs/")
    assert db_session.exec(select(AnalysisRun)).all()


def test_result_page_shows_the_required_sections(client, db_session):
    """AC5: daily metrics, ratings, lost days, distributions, rainfall."""
    location = _run(client, db_session).headers["location"]

    html = client.get(location).text

    assert "not a forecast" in html            # qualified statement (AC6)
    assert "Comparison" in html
    assert "Lost-day probability" in html
    assert "Dominant wind direction" in html   # wind rose + table
    assert "Max wind distribution" in html
    assert "Daily rainfall" in html
    assert "Daily summaries" in html
    assert "Downloads" in html
    assert "Open-Meteo" in html                # AC9


def test_result_page_lists_every_analysed_day(client, db_session):
    location = _run(client, db_session).headers["location"]

    html = client.get(location).text

    # 2 years x 3 days for one location
    for day in ("2023-10-24", "2023-10-26", "2024-10-24", "2024-10-26"):
        assert day in html


def test_multiple_locations_are_compared(client, db_session):
    """AC6: a like-for-like comparison when several are selected."""
    location = _run(client, db_session, "murtersko_more", "hvar").headers["location"]

    html = client.get(location).text

    assert "Murtersko More" in html and "Hvar" in html


def test_repeated_run_makes_no_provider_call(client, db_session, provider):
    """AC7, through the route."""
    _run(client, db_session)
    provider.calls.clear()

    _run(client, db_session)

    assert provider.calls == []


def test_missing_run_is_a_404(client):
    assert client.get("/weather-analysis/runs/9999").status_code == 404


def test_run_history_names_the_locations(client, db_session):
    """Runs over the same window and years are otherwise indistinguishable."""
    _run(client, db_session, "hvar", "rugen")
    _run(client, db_session, "kornati")

    html = client.get("/weather-analysis").text
    history = html.split("Previous analyses", 1)[1]

    assert "Hvar" in history and "Rugen" in history
    assert "Kornati" in history


def test_run_can_be_deleted(client, db_session):
    location = _run(client, db_session).headers["location"]
    run_id = int(location.rsplit("/", 1)[1])

    client.post(f"/weather-analysis/runs/{run_id}/delete", follow_redirects=False)

    assert db_session.get(AnalysisRun, run_id) is None


# --- error states -----------------------------------------------------------

def test_no_location_selected_is_an_error_not_an_empty_result(client, db_session):
    response = _run(client, db_session, location_ids=[])

    assert response.status_code == 400
    assert "at least one location" in response.text
    assert db_session.exec(select(AnalysisRun)).all() == []


def test_reversed_year_range_is_rejected(client, db_session):
    response = _run(client, db_session, first_year="2024", last_year="2020")

    assert response.status_code == 400
    assert "no later than" in response.text
    assert db_session.exec(select(AnalysisRun)).all() == []


def test_provider_failure_is_a_visible_error_state(db_session):
    """PRD §9: no misleading partial success."""
    failing = FakeProvider(fail=True)
    app.dependency_overrides[get_session] = lambda: db_session
    with patch("app.main.init_db"), patch("app.main.os.makedirs"), \
         patch("app.routers.weather_analysis.run_analysis",
               side_effect=_runner(db_session, failing)):
        with TestClient(app, raise_server_exceptions=False) as c:
            response = _run(c, db_session)
    app.dependency_overrides.clear()

    assert response.status_code == 400
    assert "provider request failed" in response.text.lower()
    assert db_session.exec(select(AnalysisRun)).all() == []
    assert db_session.exec(select(DailySummary)).all() == []


# --- exports ----------------------------------------------------------------

@pytest.mark.parametrize("path,content_type,marker", [
    ("daily.csv", "text/csv", "date,location,avg_wind_kn"),
    ("hourly.csv", "text/csv", "timestamp_utc,local_timestamp,location"),
    ("summary.json", "application/json", '"wind_bins"'),
])
def test_exports_download_with_metadata(client, db_session, path,
                                        content_type, marker):
    """AC8."""
    location = _run(client, db_session).headers["location"]
    run_id = location.rsplit("/", 1)[1]

    response = client.get(f"/weather-analysis/runs/{run_id}/export/{path}")

    assert response.status_code == 200
    assert content_type in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]
    assert marker in response.text
    assert "open-meteo" in response.text and "era5" in response.text


def test_export_of_a_missing_run_is_a_404(client):
    assert client.get(
        "/weather-analysis/runs/9999/export/daily.csv").status_code == 404


# --- location management (AC2) ---------------------------------------------

def test_locations_partial_lists_configured_locations(client):
    html = client.get("/weather-analysis/locations").text

    assert "Murtersko More" in html
    assert "Europe/Zagreb" in html


def test_location_can_be_added(client, db_session):
    response = client.post("/weather-analysis/locations", data={
        "region": "Ionian", "name": "Lefkada",
        "latitude": "38.70", "longitude": "20.65",
        "timezone": "Europe/Athens",
    })

    assert response.status_code == 200
    added = db_session.exec(
        select(AnalysisLocation).where(AnalysisLocation.name == "Lefkada")
    ).first()
    assert added is not None
    assert added.key == "lefkada"
    assert "Lefkada" in response.text


def test_added_location_accepts_comma_decimals(client, db_session):
    client.post("/weather-analysis/locations", data={
        "region": "Ionian", "name": "Kefalonia",
        "latitude": "38,25", "longitude": "20,55",
        "timezone": "Europe/Athens",
    })

    added = db_session.exec(
        select(AnalysisLocation).where(AnalysisLocation.name == "Kefalonia")
    ).first()
    assert (added.latitude, added.longitude) == (38.25, 20.55)


@pytest.mark.parametrize("field,value,message", [
    ("latitude", "999", "Latitude"),
    ("longitude", "abc", "Longitude"),
    ("timezone", "Mars/Olympus", "timezone"),
    ("name", "", "name"),
])
def test_invalid_location_is_rejected(client, db_session, field, value, message):
    before = len(db_session.exec(select(AnalysisLocation)).all())
    data = {"region": "Ionian", "name": "Lefkada", "latitude": "38.70",
            "longitude": "20.65", "timezone": "Europe/Athens"}
    data[field] = value

    response = client.post("/weather-analysis/locations", data=data)

    assert message.lower() in response.text.lower()
    assert len(db_session.exec(select(AnalysisLocation)).all()) == before


def test_location_can_be_edited(client, db_session):
    location_id = _location_id(db_session, "krk")

    response = client.post(f"/weather-analysis/locations/{location_id}", data={
        "region": "Northern Adriatic", "name": "Krk (west)",
        "latitude": "45.00", "longitude": "14.60",
        "timezone": "Europe/Zagreb",
    })

    assert response.status_code == 200
    db_session.expire_all()
    edited = db_session.get(AnalysisLocation, location_id)
    assert edited.name == "Krk (west)"
    assert edited.latitude == 45.00


def test_removing_a_location_is_soft(client, db_session):
    """AC2: removal must not delete historical results."""
    location_id = _location_id(db_session, "hvar")
    run_location = _run(client, db_session, "hvar").headers["location"]

    response = client.delete(f"/weather-analysis/locations/{location_id}")

    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.get(AnalysisLocation, location_id).active is False
    # the past run still renders with its snapshot
    assert "Hvar" in client.get(run_location).text


def test_removed_location_disappears_from_the_picker(client, db_session):
    location_id = _location_id(db_session, "falster")
    client.delete(f"/weather-analysis/locations/{location_id}")

    html = client.get("/weather-analysis").text

    assert "Falster" not in html


def test_editing_a_missing_location_is_a_404(client):
    assert client.post("/weather-analysis/locations/9999", data={
        "region": "X", "name": "Y", "latitude": "1", "longitude": "1",
        "timezone": "UTC",
    }).status_code == 404
