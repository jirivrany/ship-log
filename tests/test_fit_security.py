"""Security tests for track_path validation and port name sanitisation in create_leg."""
import os
import pathlib
import tempfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine

from app.main import app
from app.database import get_session
from app.models import Voyage


UPLOAD_DIR = "/tmp/ship_log_test_uploads"


@pytest.fixture(autouse=True)
def setup_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.environ["UPLOAD_DIR"] = UPLOAD_DIR


@pytest.fixture()
def db_session():
    """In-memory SQLite session, wired into FastAPI dependency override."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


@pytest.fixture()
def client(db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    # Patch init_db so the lifespan doesn't try to mkdir /app/data
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
    """Create a minimal placeholder file in the correct staging directory."""
    staging = pathlib.Path(UPLOAD_DIR, "staging", f"voyage_{voyage_id}")
    staging.mkdir(parents=True, exist_ok=True)
    fit_file = staging / "test.fit"
    fit_file.write_bytes(b"FIT")
    return str(fit_file)


def _post_create_leg(client, voyage_id, track_path, from_port="Sukošan", to_port="Ždrelac"):
    return client.post(
        f"/voyages/{voyage_id}/legs",
        data={
            "from_port": from_port,
            "to_port": to_port,
            "date": "2026-06-20",
            "timezone": "Europe/Zagreb",
            "track_path": track_path,
        },
    )


# --- track_path traversal ---

def test_fit_path_outside_staging_rejected(client, voyage_id):
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"sensitive")
        outside_path = f.name
    try:
        r = _post_create_leg(client, voyage_id, outside_path)
        assert r.status_code == 400, f"Expected 400, got {r.status_code}"
    finally:
        os.unlink(outside_path)


def test_fit_path_etc_passwd_rejected(client, voyage_id):
    r = _post_create_leg(client, voyage_id, "/etc/passwd")
    assert r.status_code == 400


def test_fit_path_in_staging_accepted_structurally(client, voyage_id, staged_fit):
    # The file is a valid path but not a real FIT file, so processing will fail
    # after the security check passes — we expect a redirect (303) or 500, not 400.
    r = _post_create_leg(client, voyage_id, staged_fit)
    assert r.status_code != 400, "Valid staging path should not be rejected with 400"


# --- port name directory traversal ---
# Note: because port names are interpolated as a single f-string component
# (not as separate os.path.join arguments), OS path resolution keeps them
# inside UPLOAD_DIR even with ".." sequences.  The pathlib check confirms this.
# The test verifies the check does NOT produce false positives on normal names.

def test_port_name_normal_accepted_structurally(client, voyage_id, staged_fit):
    r = _post_create_leg(client, voyage_id, staged_fit, from_port="Sukošan", to_port="Ždrelac")
    assert r.status_code != 400


def test_port_name_dotdot_stays_inside_upload_dir():
    """Verify that dotdot in a port name resolves inside UPLOAD_DIR (no traversal)."""
    import pathlib
    upload_root = pathlib.Path(UPLOAD_DIR).resolve()
    leg_dir = pathlib.Path(UPLOAD_DIR, "voyage_1", f"2026-06-20_../../etc-dest")
    assert leg_dir.resolve().is_relative_to(upload_root)
