"""Propulsion defaults to motor on upload; first/last entries are forced to
motor unless the sailor explicitly chose a different leg-wide default."""
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
from app.models import EntrySource, LogEntry, Voyage

UPLOAD_DIR = "/tmp/ship_log_test_uploads"

SAMPLE_FIT = (
    "/home/albert/work/sailing/ship_log/data/uploads/staging/voyage_1/"
    "20260620_2_🇭🇷_Uvala_Sv__Ante_-_Žirje_uvala_Žinčena.fit"
)


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


@pytest.fixture()
def voyage_id(db_session):
    v = Voyage(name="Test voyage", boat_name="Test boat")
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


def _create_leg(client, voyage_id, track_path, default_propulsion=None):
    # app.main.os.makedirs is patched to a no-op by the `client` fixture (it
    # guards the lifespan startup mkdir), so create_leg's own leg_dir mkdir
    # is a no-op too — pre-create the destination dir the test will move into.
    leg_dir = pathlib.Path(UPLOAD_DIR, f"voyage_{voyage_id}", "2026-06-20_Sukošan-Ždrelac")
    leg_dir.mkdir(parents=True, exist_ok=True)

    data = {
        "from_port": "Sukošan",
        "to_port": "Ždrelac",
        "date": "2026-06-20",
        "timezone": "Europe/Zagreb",
        "track_path": track_path,
    }
    if default_propulsion is not None:
        data["default_propulsion"] = default_propulsion
    return client.post(f"/voyages/{voyage_id}/legs", data=data)


@pytest.mark.skipif(not os.path.exists(SAMPLE_FIT), reason="sample FIT fixture not available")
def test_no_propulsion_field_defaults_all_entries_to_motor(client, voyage_id, staged_fit, db_session):
    r = _create_leg(client, voyage_id, staged_fit)
    assert r.status_code == 200
    assert "/legs/" in str(r.url)

    entries = db_session.exec(select(LogEntry)).all()
    assert entries, "expected generated log entries"
    assert all(e.propulsion.value == "motor" for e in entries)


@pytest.mark.skipif(not os.path.exists(SAMPLE_FIT), reason="sample FIT fixture not available")
def test_explicit_sail_default_respected_except_when_forced(client, voyage_id, staged_fit, db_session):
    r = _create_leg(client, voyage_id, staged_fit, default_propulsion="sail")
    assert r.status_code == 200
    assert "/legs/" in str(r.url)

    entries = db_session.exec(
        select(LogEntry).order_by(LogEntry.timestamp)
    ).all()
    assert entries
    # Explicit non-motor leg default is respected, including at the anchors.
    assert all(e.propulsion.value == "sail" for e in entries)


@pytest.mark.skipif(not os.path.exists(SAMPLE_FIT), reason="sample FIT fixture not available")
def test_first_and_last_forced_motor_when_default_is_motor(client, voyage_id, staged_fit, db_session):
    r = _create_leg(client, voyage_id, staged_fit, default_propulsion="motor")
    assert r.status_code == 200
    assert "/legs/" in str(r.url)

    entries = db_session.exec(
        select(LogEntry).order_by(LogEntry.timestamp)
    ).all()
    assert entries[0].propulsion.value == "motor"
    assert entries[-1].propulsion.value == "motor"
