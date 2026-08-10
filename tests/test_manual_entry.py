"""Adding a manual entry by clicking the track must tolerate missing readings.

FIT track points routinely lack course and air temperature, and the very first
sample often lacks speed. Those fields used to be declared as floats on the
form, so a blank value came back as 422 and the point was never added.
"""
import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy.pool import StaticPool
from unittest.mock import patch

from app.main import app
from app.database import get_session
from app.models import EntrySource, Leg, LogEntry, Voyage


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
def leg(db_session):
    voyage = Voyage(name="Test voyage", boat_name="Test boat")
    db_session.add(voyage)
    db_session.commit()
    db_session.refresh(voyage)

    leg = Leg(
        voyage_id=voyage.id,
        from_port="Vodice",
        to_port="Žirje",
        date="2026-06-20",
        timezone="Europe/Prague",
    )
    db_session.add(leg)
    db_session.commit()
    db_session.refresh(leg)
    return leg


def test_adds_entry_with_full_readings(client, db_session, leg):
    resp = client.post(
        f"/legs/{leg.id}/entries",
        data={
            "ts": "2026-06-20T10:15:00+00:00",
            "lat": "43.65",
            "lon": "15.85",
            "course": "212",
            "speed": "5.4",
            "dist_nm": "12.3",
            "temp": "24.5",
        },
    )
    assert resp.status_code == 200

    entry = db_session.exec(select(LogEntry).where(LogEntry.leg_id == leg.id)).one()
    assert entry.source == EntrySource.manual
    assert entry.course == 212
    assert entry.speed == 5.4
    assert entry.log_value == 12.3
    assert entry.air_temperature == 24.5


def test_adds_entry_when_optional_fields_omitted(client, db_session, leg):
    """The client omits fields it has no value for."""
    resp = client.post(
        f"/legs/{leg.id}/entries",
        data={"ts": "2026-06-20T10:15:00+00:00", "lat": "43.65", "lon": "15.85"},
    )
    assert resp.status_code == 200

    entry = db_session.exec(select(LogEntry).where(LogEntry.leg_id == leg.id)).one()
    assert entry.lat == 43.65
    assert entry.course is None
    assert entry.speed is None
    assert entry.log_value is None
    assert entry.air_temperature is None


@pytest.mark.parametrize("blank", ["", "null", "undefined"])
def test_adds_entry_when_optional_fields_are_blank(client, db_session, leg, blank):
    """This is the exact payload that used to 422."""
    resp = client.post(
        f"/legs/{leg.id}/entries",
        data={
            "ts": "2026-06-20T10:15:00+00:00",
            "lat": "43.65",
            "lon": "15.85",
            "course": blank,
            "speed": blank,
            "dist_nm": blank,
            "temp": blank,
        },
    )
    assert resp.status_code == 200, resp.text

    entry = db_session.exec(select(LogEntry).where(LogEntry.leg_id == leg.id)).one()
    assert entry.course is None
    assert entry.speed is None
    assert entry.log_value is None
    assert entry.air_temperature is None


def test_partial_readings_are_preserved(client, db_session, leg):
    """A missing temperature must not discard the course that was present."""
    resp = client.post(
        f"/legs/{leg.id}/entries",
        data={
            "ts": "2026-06-20T10:15:00+00:00",
            "lat": "43.65",
            "lon": "15.85",
            "course": "212",
            "speed": "5.4",
            "temp": "",
        },
    )
    assert resp.status_code == 200

    entry = db_session.exec(select(LogEntry).where(LogEntry.leg_id == leg.id)).one()
    assert entry.course == 212
    assert entry.speed == 5.4
    assert entry.air_temperature is None
