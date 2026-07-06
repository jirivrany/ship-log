"""Strava activity descriptions are imported as a quick note timestamped
at the leg's arrival time, on both the create-leg and attach-track paths."""
import json
import os
import pathlib
import shutil

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from app.main import app
from app.database import get_session
from app.models import EntrySource, Leg, LogEntry, Voyage
from app.routers.legs import _generate_gps_entries

UPLOAD_DIR = "/tmp/ship_log_test_uploads"

SAMPLE_FIT = (
    "/home/albert/work/sailing/ship_log/data/uploads/staging/voyage_1/"
    "20260620_2_🇭🇷_Uvala_Sv__Ante_-_Žirje_uvala_Žinčena.fit"
)

needs_sample = pytest.mark.skipif(
    not os.path.exists(SAMPLE_FIT), reason="sample FIT fixture not available"
)


@pytest.fixture(autouse=True)
def setup_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.environ["UPLOAD_DIR"] = UPLOAD_DIR


# --- unit: _generate_gps_entries ---

@needs_sample
def test_description_becomes_note_at_arrival_time():
    entries = _generate_gps_entries(
        leg_id=1, track_path=SAMPLE_FIT,
        default_propulsion="motor", default_wind_direction=None, default_wind_force=None,
        description="Great sail, force 4 from NW.",
    )
    notes = [e for e in entries if e.source == EntrySource.quick_note]
    assert len(notes) == 1
    assert notes[0].notes == "Great sail, force 4 from NW."

    gps_entries = [e for e in entries if e.source != EntrySource.quick_note]
    last_gps = max(gps_entries, key=lambda e: e.timestamp)
    assert notes[0].timestamp == last_gps.timestamp
    assert notes[0].lat == last_gps.lat and notes[0].lon == last_gps.lon


@needs_sample
def test_no_description_means_no_note():
    entries = _generate_gps_entries(
        leg_id=1, track_path=SAMPLE_FIT,
        default_propulsion="motor", default_wind_direction=None, default_wind_force=None,
    )
    assert not [e for e in entries if e.source == EntrySource.quick_note]


# --- end-to-end: create_leg with an imported description ---

@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    with patch("app.main.init_db"), patch("app.main.os.makedirs"):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def voyage_id(db_session):
    v = Voyage(name="Test voyage", boat="Test boat")
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v.id


@pytest.fixture()
def staged_fit(voyage_id):
    staging = pathlib.Path(UPLOAD_DIR, "staging", f"voyage_{voyage_id}")
    staging.mkdir(parents=True, exist_ok=True)
    dest = staging / "sample.fit"
    shutil.copyfile(SAMPLE_FIT, dest)
    return str(dest)


@needs_sample
def test_create_leg_persists_description_as_note(client, voyage_id, staged_fit, db_session):
    leg_dir = pathlib.Path(UPLOAD_DIR, f"voyage_{voyage_id}", "2026-06-20_Sukošan-Ždrelac")
    leg_dir.mkdir(parents=True, exist_ok=True)

    r = client.post(f"/voyages/{voyage_id}/legs", data={
        "from_port": "Sukošan",
        "to_port": "Ždrelac",
        "date": "2026-06-20",
        "timezone": "Europe/Zagreb",
        "track_path": staged_fit,
        "description": "Windy crossing, reefed main early.",
    })
    assert r.status_code == 200
    assert "/legs/" in str(r.url)

    notes = db_session.exec(
        select(LogEntry).where(LogEntry.source == EntrySource.quick_note)
    ).all()
    assert len(notes) == 1
    assert notes[0].notes == "Windy crossing, reefed main early."


@needs_sample
def test_create_leg_without_description_has_no_note(client, voyage_id, staged_fit, db_session):
    leg_dir = pathlib.Path(UPLOAD_DIR, f"voyage_{voyage_id}", "2026-06-20_Sukošan-Ždrelac")
    leg_dir.mkdir(parents=True, exist_ok=True)

    r = client.post(f"/voyages/{voyage_id}/legs", data={
        "from_port": "Sukošan",
        "to_port": "Ždrelac",
        "date": "2026-06-20",
        "timezone": "Europe/Zagreb",
        "track_path": staged_fit,
    })
    assert r.status_code == 200
    assert db_session.exec(
        select(LogEntry).where(LogEntry.source == EntrySource.quick_note)
    ).all() == []


# --- confirm-form rendering: description preview appears only when present ---

def _write_strava_bundle(tmp_path, description=None):
    bundle = {
        "activity": {
            "id": 555, "name": "Sukošan - Ždrelac",
            "start_date": "2026-06-20T08:00:00Z", "distance": 5556.0,
            "description": description,
        },
        "streams": {
            "time": {"data": [0, 30, 60]},
            "latlng": {"data": [[44.0, 15.0], [44.001, 15.0], [44.002, 15.0]]},
            "velocity_smooth": {"data": [2.5, 2.5, 2.5]},
            "distance": {"data": [0, 77, 154]},
            "temp": {"data": [30, 30, 30]},
        },
        "laps": [],
    }
    path = tmp_path / "strava_555.json"
    path.write_text(json.dumps(bundle))
    return path


def test_confirm_form_shows_description_when_present(client, voyage_id, tmp_path):
    staging = pathlib.Path(UPLOAD_DIR, "staging", f"voyage_{voyage_id}")
    staging.mkdir(parents=True, exist_ok=True)
    bundle_path = _write_strava_bundle(tmp_path, description="Great sail, force 4 from NW.")
    shutil.copy(bundle_path, staging / "strava_555.json")

    with patch("app.routers.strava.strava_api.fetch_activity_bundle") as mock_fetch:
        mock_fetch.return_value = json.loads(bundle_path.read_text())
        r = client.post(f"/voyages/{voyage_id}/strava/555/preview")

    assert r.status_code == 200
    assert "Leg summary" in r.text
    assert "Great sail, force 4 from NW." in r.text


def test_confirm_form_hides_description_when_absent(client, voyage_id, tmp_path):
    staging = pathlib.Path(UPLOAD_DIR, "staging", f"voyage_{voyage_id}")
    staging.mkdir(parents=True, exist_ok=True)
    bundle_path = _write_strava_bundle(tmp_path, description=None)

    with patch("app.routers.strava.strava_api.fetch_activity_bundle") as mock_fetch:
        mock_fetch.return_value = json.loads(bundle_path.read_text())
        r = client.post(f"/voyages/{voyage_id}/strava/555/preview")

    assert r.status_code == 200
    assert "Leg summary" not in r.text
