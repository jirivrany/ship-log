"""User profile: name save/update and cross-voyage totals split by skipper flag."""
import os
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import get_session
from app.models import EntrySource, Leg, LogEntry, PropulsionType, UserProfile, Voyage

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


def _add_voyage_with_leg(session, name, was_skipper, entries):
    """entries: [(minutes_offset, log_value, propulsion), ...]"""
    voyage = Voyage(name=name, boat_name="Diana", was_skipper=was_skipper)
    session.add(voyage)
    session.commit()
    session.refresh(voyage)
    leg = Leg(voyage_id=voyage.id, from_port="A", to_port="B", date="2026-06-20")
    session.add(leg)
    session.commit()
    session.refresh(leg)
    start = datetime(2026, 6, 20, 8, 0)
    for minutes, log_value, propulsion in entries:
        session.add(LogEntry(
            leg_id=leg.id,
            timestamp=start + timedelta(minutes=minutes),
            source=EntrySource.manual,
            log_value=log_value,
            propulsion=PropulsionType(propulsion),
        ))
    session.commit()
    return voyage


# --- totals ---

def test_profile_splits_skipper_and_crew_totals(client, db_session):
    # skippered: 10 Nm sail over 2:00
    _add_voyage_with_leg(db_session, "Skippered", True, [
        (0, 0.0, "sail"), (120, 10.0, "sail"),
    ])
    # crew: 5 Nm motor over 1:00
    _add_voyage_with_leg(db_session, "Crewed", False, [
        (0, 0.0, "motor"), (60, 5.0, "motor"),
    ])

    r = client.get("/profile")
    assert r.status_code == 200
    # overall: 15 Nm, 10 sail + 5 motor, 3:00 total
    assert "15.0 Nm" in r.text
    assert "10.0 Nm" in r.text
    assert "5.0 Nm" in r.text
    assert "3:00" in r.text
    assert "As skipper" in r.text
    assert "As crew member" in r.text


def test_profile_all_crew_hides_skipper_section(client, db_session):
    _add_voyage_with_leg(db_session, "Crewed", False, [
        (0, 0.0, "motor"), (60, 5.0, "motor"),
    ])
    r = client.get("/profile")
    assert "As skipper" not in r.text
    assert "As crew member" in r.text


def test_profile_empty_db_renders_zeros(client):
    r = client.get("/profile")
    assert r.status_code == 200
    assert "0.0 Nm" in r.text
    assert "0:00" in r.text


def test_profile_voyage_without_entries_does_not_crash(client, db_session):
    _add_voyage_with_leg(db_session, "Empty", True, [])
    r = client.get("/profile")
    assert r.status_code == 200
    assert "0.0 Nm" in r.text


# --- name ---

def test_save_and_show_profile_name(client, db_session):
    r = client.post("/profile", data={"name": "Jiri"}, follow_redirects=True)
    assert r.status_code == 200
    assert "Jiri" in r.text
    assert db_session.exec(select(UserProfile)).one().name == "Jiri"


def test_save_name_again_updates_single_row(client, db_session):
    client.post("/profile", data={"name": "Jiri"})
    client.post("/profile", data={"name": "Jiří"})
    profiles = db_session.exec(select(UserProfile)).all()
    assert len(profiles) == 1
    assert profiles[0].name == "Jiří"
