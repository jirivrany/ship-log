"""Pairing a Strava activity with a manually created (trackless) leg.

Covers the leg-scoped picker window and the route fields the confirm form
offers back on the attach path.
"""
import json
import os
import pathlib
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import get_session
from app.models import Leg, Voyage
from app.routers.strava import _leg_window, _voyage_window

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
    # Only init_db is patched: `app.main.os` is the same module object the
    # routers use, so patching os.makedirs there would also stop attach_track
    # from creating the leg directory it moves the track into.
    with patch("app.main.init_db"):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def manual_leg(db_session):
    """A leg created by hand: ports typed by the user, no track attached."""
    voyage = Voyage(name="Test Liberec", boat_name="Test boat",
                    start_date="2026-08-01", end_date="2026-08-30")
    db_session.add(voyage)
    db_session.commit()
    db_session.refresh(voyage)

    leg = Leg(voyage_id=voyage.id, from_port="Test A", to_port="Test B",
              date="2026-08-17", timezone="UTC", track_path=None)
    db_session.add(leg)
    db_session.commit()
    db_session.refresh(leg)
    return leg


# --- picker window ---

def test_leg_window_uses_same_margins_as_voyage_window():
    """A one-day leg gets the -1/+2 day margins, so the activity can't fall
    outside the window on a timezone offset."""
    after, before = _leg_window(Leg(from_port="a", to_port="b", date="2026-08-17"))
    # 2026-08-16 00:00 UTC .. 2026-08-19 00:00 UTC
    assert (before - after) == 3 * 86400
    assert _voyage_window(
        Voyage(name="v", boat_name="b", start_date="2026-08-17", end_date="2026-08-17")
    ) == (after, before)


def test_leg_window_without_date_falls_back_to_voyage():
    assert _leg_window(Leg(from_port="a", to_port="b", date="")) == (None, None)


def test_leg_picker_requests_leg_date_window(client, manual_leg):
    """The leg picker must narrow to the leg's date rather than the whole
    voyage — otherwise pairing one leg means scrolling the entire charter."""
    captured = {}

    def fake_fetch(*, after=None, before=None, **kwargs):
        captured["after"], captured["before"] = after, before
        return type("R", (), {"activities": [], "scanned": 0, "truncated": False})()

    with patch("app.strava_api.fetch_sail_activities", side_effect=fake_fetch):
        r = client.get(f"/legs/{manual_leg.id}/strava")

    assert r.status_code == 200
    assert (captured["after"], captured["before"]) == _leg_window(manual_leg)
    # ...and not the voyage's much wider window
    assert (captured["after"], captured["before"]) != _voyage_window(manual_leg.voyage)


def test_leg_page_links_to_strava_picker(client, manual_leg):
    """The whole reported bug: a trackless leg offered no way to reach Strava."""
    r = client.get(f"/legs/{manual_leg.id}")
    assert r.status_code == 200
    assert f'href="/legs/{manual_leg.id}/strava"' in r.text
    assert f'href="/legs/{manual_leg.id}/attach-track"' in r.text


# --- route fields on attach ---

def _staged_bundle(leg_id: int) -> str:
    """A minimal Strava bundle parked in this leg's staging area."""
    staging = pathlib.Path(UPLOAD_DIR, "staging", f"leg_{leg_id}")
    staging.mkdir(parents=True, exist_ok=True)
    path = staging / "strava_999.json"
    path.write_text(json.dumps({
        "activity": {
            "id": 999,
            "name": "Sukošan - Ždrelac",
            "start_date": "2026-06-20T08:00:00Z",
            "distance": 5556.0,
        },
        "streams": {
            "time": {"data": [i * 30 for i in range(12)]},
            "latlng": {"data": [[44.0 + i * 0.000694, 15.0] for i in range(12)]},
            "velocity_smooth": {"data": [2.5722] * 12},
            "distance": {"data": [i * 77.2 for i in range(12)]},
        },
        "laps": [],
    }))
    return str(path)


def test_attach_applies_edited_route_and_files_track_under_new_name(client, manual_leg, db_session):
    """Accepting the parsed route must update the leg *and* place the track in
    a directory named for the new ports — the two are derived together."""
    track_path = _staged_bundle(manual_leg.id)

    r = client.post(f"/legs/{manual_leg.id}/attach-track", data={
        "track_path": track_path,
        "strava_activity_id": 999,
        "date": "2026-06-20",
        "from_port": "Sukošan",
        "to_port": "Ždrelac",
        "default_propulsion": "motor",
    }, follow_redirects=False)
    assert r.status_code == 303

    db_session.refresh(manual_leg)
    assert (manual_leg.from_port, manual_leg.to_port) == ("Sukošan", "Ždrelac")
    assert manual_leg.date == "2026-06-20"
    assert manual_leg.strava_activity_id == 999
    # The stored track must live under the *new* route, not "Test A-Test B"
    assert "2026-06-20_Sukošan-Ždrelac" in manual_leg.track_path
    assert os.path.exists(manual_leg.track_path)


def test_attach_with_blank_route_keeps_manual_values(client, manual_leg, db_session):
    """A track that parses no ports must not blank out hand-typed ones."""
    track_path = _staged_bundle(manual_leg.id)

    r = client.post(f"/legs/{manual_leg.id}/attach-track", data={
        "track_path": track_path,
        "date": "",
        "from_port": "",
        "to_port": "",
        "default_propulsion": "motor",
    }, follow_redirects=False)
    assert r.status_code == 303

    db_session.refresh(manual_leg)
    assert (manual_leg.from_port, manual_leg.to_port) == ("Test A", "Test B")
    assert manual_leg.date == "2026-08-17"
    assert manual_leg.track_path is not None
