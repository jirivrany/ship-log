"""Shared map module: both map-bearing pages must build their map through
ShipLogMap so the OpenSeaMap overlay, the layer control and the licence
attribution stay in one place.

Leaflet itself cannot run here, so these guard the wiring and the CC-BY-SA
obligation — the parts that rot silently when a template is edited.
"""
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool

from app.main import app
from app.database import get_session
from app.models import Leg, Voyage

UPLOAD_DIR = "/tmp/ship_log_test_uploads"

MAP_JS = "app/static/js/map.js"


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
def voyage_with_leg(db_session):
    voyage = Voyage(name="Chorvatsko 2026", boat_name="Diana")
    db_session.add(voyage)
    db_session.commit()
    db_session.refresh(voyage)

    leg = Leg(
        voyage_id=voyage.id,
        from_port="Split",
        to_port="Hvar",
        date="2026-06-01",
        timezone="Europe/Zagreb",
    )
    db_session.add(leg)
    db_session.commit()
    db_session.refresh(leg)
    return voyage, leg


# --- the shared module is reachable everywhere a map can appear ---

def test_map_module_is_loaded_for_every_page(client):
    """map.js ships from base.html, next to Leaflet, so any page with a map
    container can call it without extra plumbing."""
    html = client.get("/").text

    assert "/static/js/map.js" in html


# --- both maps are built through the shared module ---

def test_voyage_map_is_built_through_shared_module(client, voyage_with_leg):
    voyage, _ = voyage_with_leg

    html = client.get(f"/voyages/{voyage.id}").text

    assert "ShipLogMap.create('voyage-map')" in html
    # the old per-page tile layer must not creep back in
    assert "tile.openstreetmap.org" not in html


def test_leg_map_is_built_through_shared_module(client, voyage_with_leg):
    _, leg = voyage_with_leg

    html = client.get(f"/legs/{leg.id}").text

    assert "ShipLogMap.create('map')" in html
    assert "tile.openstreetmap.org" not in html


def test_leg_map_keeps_its_adriatic_fallback_view(client, voyage_with_leg):
    """A leg with no track and no positioned entries still needs a sensible
    starting view rather than an unpositioned grey map."""
    _, leg = voyage_with_leg

    html = client.get(f"/legs/{leg.id}").text

    assert "setView([43.5, 16.0], 8)" in html


# --- the overlay itself ---

def test_seamark_overlay_points_at_openseamap_over_https():
    """Mixed content would be blocked outright on the deployed HTTPS site."""
    js = open(MAP_JS).read()

    assert "https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png" in js
    assert "http://tiles.openseamap.org" not in js


def test_seamark_overlay_caps_native_zoom():
    """Seamark coverage runs out above ~z17 and the tiles come back empty, so
    Leaflet must upscale instead of requesting blanks."""
    js = open(MAP_JS).read()

    assert "maxNativeZoom" in js
    assert "SEAMARK_MAX_NATIVE_ZOOM = 17" in js


def test_seamark_overlay_defaults_to_on():
    """A sailing logbook should show charted marks without hunting for a toggle."""
    js = open(MAP_JS).read()

    assert "!== 'off'" in js


def test_both_tile_sources_are_attributed():
    """OSM requires attribution; OpenSeaMap tiles are CC-BY-SA."""
    js = open(MAP_JS).read()

    assert "OpenStreetMap" in js
    assert "OpenSeaMap" in js
    assert "CC-BY-SA" in js
