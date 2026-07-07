"""Voyage CRUD: create via shared form parsing, edit prefill + save,
cascading delete including files on disk."""
import os
import pathlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import get_session
from app.models import Leg, LogEntry, EntrySource, Voyage

UPLOAD_DIR = "/tmp/ship_log_test_uploads"


@pytest.fixture(autouse=True)
def setup_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.environ["UPLOAD_DIR"] = UPLOAD_DIR


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


# --- create ---

def test_index_renders_create_form(client):
    """The shared form partial must render without a voyage in context."""
    r = client.get("/")
    assert r.status_code == 200
    assert 'name="skipper"' in r.text
    assert "Create Voyage" in r.text


def test_create_voyage_full_form(client, db_session):
    r = client.post("/voyages", data={
        "name": "Chorvatsko 2025",
        "start_date": "2025-10-04",
        "end_date": "2025-10-11",
        "boat_name": "Diana",
        "boat_maker": "Bavaria",
        "boat_model": "Cruiser 37",
        "year_built": "2016",
        "skipper": "Jiri",
        "crew": "posádka",
        "length_m": "10.5",
        "max_persons": "8",
    })
    assert r.status_code == 200 and "/voyages/" in str(r.url)

    v = db_session.exec(select(Voyage)).one()
    assert v.name == "Chorvatsko 2025"
    assert v.start_date == "2025-10-04" and v.end_date == "2025-10-11"
    assert v.boat_name == "Diana"
    assert v.boat_maker == "Bavaria" and v.boat_model == "Cruiser 37"
    assert v.year_built == 2016
    assert v.skipper == "Jiri"
    assert v.length_m == 10.5 and v.max_persons == 8
    assert v.boat_label == "Diana — Bavaria Cruiser 37 (2016)"


def test_create_voyage_requires_name_and_boat_name(client, db_session):
    r = client.post("/voyages", data={"name": "No boat"})
    assert r.status_code == 400
    assert db_session.exec(select(Voyage)).all() == []


# --- edit ---

@pytest.fixture()
def voyage(db_session):
    v = Voyage(name="Original", boat_name="Bavaria 34", skipper="Jiri", length_m=10.5)
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v


def test_edit_form_prefilled(client, voyage):
    r = client.get(f"/voyages/{voyage.id}/edit")
    assert r.status_code == 200
    assert 'value="Original"' in r.text
    assert 'value="Jiri"' in r.text
    assert 'value="10.5"' in r.text


def test_edit_saves_and_preserves_untouched(client, voyage, db_session):
    r = client.post(f"/voyages/{voyage.id}/edit", data={
        "name": "Original",
        "boat_name": "Bavaria 34",
        "start_date": "2025-10-04",
        "end_date": "2025-10-11",
        "skipper": "Jiri",
        "length_m": "10.5",
    })
    assert r.status_code == 200 and f"/voyages/{voyage.id}" in str(r.url)

    db_session.refresh(voyage)
    assert voyage.start_date == "2025-10-04"
    assert voyage.skipper == "Jiri"
    assert voyage.length_m == 10.5


def test_edit_dates_drive_strava_window(client, voyage, db_session):
    from app.routers.strava import _voyage_window

    client.post(f"/voyages/{voyage.id}/edit", data={
        "name": "Original", "boat_name": "Bavaria 34",
        "start_date": "2025-10-04", "end_date": "2025-10-11",
    })
    db_session.refresh(voyage)
    after, before = _voyage_window(voyage)
    assert after is not None and before is not None and after < before


def test_create_voyage_was_skipper_checked(client, db_session):
    client.post("/voyages", data={
        "name": "Skippered", "boat_name": "Diana", "was_skipper": "1",
    })
    v = db_session.exec(select(Voyage)).one()
    assert v.was_skipper is True


def test_edit_unchecked_checkbox_clears_was_skipper(client, voyage, db_session):
    voyage.was_skipper = True
    db_session.add(voyage)
    db_session.commit()

    # unchecked checkbox is absent from the POST body
    client.post(f"/voyages/{voyage.id}/edit", data={
        "name": "Original", "boat_name": "Bavaria 34",
    })
    db_session.refresh(voyage)
    assert voyage.was_skipper is False


def test_edit_form_renders_was_skipper_checked(client, voyage, db_session):
    voyage.was_skipper = True
    db_session.add(voyage)
    db_session.commit()

    r = client.get(f"/voyages/{voyage.id}/edit")
    assert 'name="was_skipper"' in r.text
    assert "checked" in r.text


def test_edit_blank_field_clears_value(client, voyage, db_session):
    client.post(f"/voyages/{voyage.id}/edit", data={
        "name": "Original", "boat_name": "Bavaria 34", "skipper": "",
    })
    db_session.refresh(voyage)
    assert voyage.skipper is None


def test_edit_missing_voyage_404(client):
    assert client.get("/voyages/999/edit").status_code == 404
    r = client.post("/voyages/999/edit", data={"name": "x", "boat_name": "y"})
    assert r.status_code == 404


# --- delete ---

def test_delete_cascades_rows_and_files(client, voyage, db_session):
    from datetime import datetime

    leg = Leg(voyage_id=voyage.id, from_port="A", to_port="B", date="2026-06-20",
              strava_activity_id=424242)
    db_session.add(leg)
    db_session.commit()
    db_session.refresh(leg)
    db_session.add(LogEntry(leg_id=leg.id, timestamp=datetime(2026, 6, 20, 8, 0),
                            source=EntrySource.manual))
    db_session.commit()

    voyage_dir = pathlib.Path(UPLOAD_DIR, f"voyage_{voyage.id}")
    staging_dir = pathlib.Path(UPLOAD_DIR, "staging", f"voyage_{voyage.id}")
    for d in (voyage_dir, staging_dir):
        d.mkdir(parents=True, exist_ok=True)
        (d / "track.fit").write_bytes(b"FIT")

    r = client.delete(f"/voyages/{voyage.id}")
    assert r.status_code == 200
    assert r.headers["HX-Redirect"] == "/"

    assert db_session.exec(select(Voyage)).all() == []
    assert db_session.exec(select(Leg)).all() == []       # strava id freed too
    assert db_session.exec(select(LogEntry)).all() == []
    assert not voyage_dir.exists()
    assert not staging_dir.exists()


def test_delete_missing_voyage_is_noop(client):
    r = client.delete("/voyages/999")
    assert r.status_code == 200
