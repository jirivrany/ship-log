"""GPX adapter tests against a real Garmin Connect export, plus the
end-to-end upload flow (preview -> create) with a GPX file."""
import os
import pathlib
import shutil
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import get_session
from app.models import Leg, LogEntry, TrackSource, Voyage
from app.processors.gpx_track import (
    _ports_from_filename,
    ports_from_name,
    parse_gpx_metadata,
    parse_gpx_track,
)

UPLOAD_DIR = "/tmp/ship_log_test_uploads"

SAMPLE_GPX = (
    "/home/albert/work/sailing/gpx_merger/data/2026_06_Sukosan/"
    "0620_1_Sukosan-Zdrelac.gpx"
)

needs_gpx = pytest.mark.skipif(
    not os.path.exists(SAMPLE_GPX), reason="sample GPX fixture not available"
)


# --- adapter: real Garmin Connect file ---

@needs_gpx
def test_parse_real_gpx_track():
    track = parse_gpx_track(SAMPLE_GPX)
    assert len(track.track_points) > 10

    times = [tp.timestamp for tp in track.track_points]
    assert times == sorted(times)
    # ~2 s raw recording interval must be sampled down to >= 30 s
    assert all((b - a).total_seconds() >= 30 for a, b in zip(times, times[1:]))

    for tp in track.track_points:
        assert 43.0 < tp.lat < 45.0 and 14.0 < tp.lon < 16.0
        assert tp.speed_knots >= 0.0          # derived — GPX has no speed field
        assert tp.raw_distance_nm is None     # GPX has no distance field

    # Garmin TrackPointExtension atemp is picked up
    assert any(tp.air_temperature is not None for tp in track.track_points)
    # boat actually moved
    assert track.track_points[-1].distance_nm > 1.0


@needs_gpx
def test_parse_real_gpx_metadata():
    meta = parse_gpx_metadata(SAMPLE_GPX, os.path.basename(SAMPLE_GPX))
    assert meta.timezone == "Europe/Zagreb"
    assert meta.date == "2026-06-20"          # local calendar date from first point
    assert meta.start_time is not None
    assert meta.total_distance_nm and meta.total_distance_nm > 1.0
    # Garmin auto-title has no 'From - To' shape -> ports come from the filename
    assert meta.from_port == "Sukosan"
    assert meta.to_port == "Zdrelac"


# --- port name parsing ---

def test_ports_from_name_matches_from_to():
    assert ports_from_name("Zadar - Muline") == ("Zadar", "Muline")
    assert ports_from_name("Zadar – Muline") == ("Zadar", "Muline")  # en dash


def test_ports_from_name_rejects_auto_titles():
    assert ports_from_name("Bibinje Plavba lodí") == (None, None)
    assert ports_from_name(None) == (None, None)


def test_ports_from_filename():
    assert _ports_from_filename("0620_1_Sukosan-Zdrelac.gpx") == ("Sukosan", "Zdrelac")
    assert _ports_from_filename("0621_2_Sv_Ante-Tratinska.gpx") == ("Sv Ante", "Tratinska")
    assert _ports_from_filename("nodash.gpx") == (None, None)


# --- end-to-end upload flow ---

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
    v = Voyage(name="Test voyage", boat="Test boat")
    db_session.add(v)
    db_session.commit()
    db_session.refresh(v)
    return v.id


@pytest.fixture()
def staged_gpx(voyage_id):
    staging = pathlib.Path(UPLOAD_DIR, "staging", f"voyage_{voyage_id}")
    staging.mkdir(parents=True, exist_ok=True)
    dest = staging / "0620_1_Sukosan-Zdrelac.gpx"
    shutil.copyfile(SAMPLE_GPX, dest)
    return str(dest)


@needs_gpx
def test_preview_accepts_gpx_and_prefills(client, voyage_id):
    with open(SAMPLE_GPX, "rb") as f:
        r = client.post(
            f"/voyages/{voyage_id}/legs/preview",
            files={"track_file": ("0620_1_Sukosan-Zdrelac.gpx", f, "application/gpx+xml")},
        )
    assert r.status_code == 200
    assert "Sukosan" in r.text and "Zdrelac" in r.text
    assert "2026-06-20" in r.text
    assert "Europe/Zagreb" in r.text
    # GPX carries no activity description — the note-preview section must not appear
    assert "Leg summary" not in r.text


def test_preview_rejects_unsupported_extension(client, voyage_id):
    r = client.post(
        f"/voyages/{voyage_id}/legs/preview",
        files={"track_file": ("notes.txt", b"not a track", "text/plain")},
    )
    assert r.status_code == 400


@needs_gpx
def test_create_leg_from_gpx(client, voyage_id, staged_gpx, db_session):
    leg_dir = pathlib.Path(UPLOAD_DIR, f"voyage_{voyage_id}", "2026-06-20_Sukosan-Zdrelac")
    leg_dir.mkdir(parents=True, exist_ok=True)

    r = client.post(
        f"/voyages/{voyage_id}/legs",
        data={
            "from_port": "Sukosan",
            "to_port": "Zdrelac",
            "date": "2026-06-20",
            "timezone": "Europe/Zagreb",
            "track_path": staged_gpx,
        },
    )
    assert r.status_code == 200
    assert "/legs/" in str(r.url)

    leg = db_session.exec(select(Leg)).one()
    assert leg.track_source == TrackSource.gpx
    assert leg.track_path.endswith(".gpx")

    entries = db_session.exec(select(LogEntry).order_by(LogEntry.timestamp)).all()
    assert entries, "expected generated log entries"
    # start/end anchors exist and default propulsion applies
    assert all(e.propulsion.value == "motor" for e in entries)
    # derived speed made it into the entries
    assert any(e.speed is not None and e.speed > 0 for e in entries)
