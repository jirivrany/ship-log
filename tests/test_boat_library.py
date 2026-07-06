"""Curated boat library: JSON integrity, lookup matching, prefill endpoint."""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.boat_library import SPEC_FIELDS, _library, lookup


@pytest.fixture()
def client():
    with patch("app.main.init_db"), patch("app.main.os.makedirs"):
        with TestClient(app) as c:
            yield c


# --- JSON integrity ---

def test_every_boat_references_a_known_model():
    for boat in _library()["boats"]:
        match = lookup(name=boat["name"])
        assert match is not None, boat["name"]
        assert "length_m" in match["fields"], f"{boat['name']}: model specs missing"


def test_model_spec_fields_are_known_voyage_columns():
    allowed = set(SPEC_FIELDS) | {"maker", "model", "built", "notes", "sources"}
    for model in _library()["models"]:
        assert set(model) <= allowed, f"{model['maker']} {model['model']}: unknown keys {set(model) - allowed}"


# --- lookup ---

def test_lookup_by_name_ignores_case_and_diacritics():
    match = lookup(name="  eirene ")
    assert match["fields"]["boat_maker"] == "Bavaria"
    assert match["fields"]["boat_model"] == "Cruiser 37"
    assert match["fields"]["year_built"] == 2017
    assert match["fields"]["length_m"] == 11.3
    assert match["label"] == "Eirené — Bavaria Cruiser 37 (2017)"


def test_lookup_by_name_wins_over_maker_model():
    # name identifies the boat; a mismatched typed model must not override it
    match = lookup(name="Namaste", maker="Bavaria", model="Cruiser 45")
    assert match["fields"]["boat_model"] == "Cruiser 34"
    assert match["fields"]["length_m"] == 9.99


def test_lookup_by_maker_model_without_known_name():
    match = lookup(name="Unknown Hull", maker="bavaria", model="CRUISER 45")
    assert "boat_maker" not in match["fields"]  # no hull match -> no identity fill
    assert match["fields"]["length_m"] == 14.27
    assert match["label"] == "bavaria CRUISER 45"


def test_lookup_omits_null_specs():
    match = lookup(name="La Gomera")
    assert "water_tank_l" not in match["fields"]  # unknown for the Maxus
    assert "swing keel" in match["note"]


def test_lookup_no_match_returns_none():
    assert lookup(name="Black Pearl", maker="Flying", model="Dutchman") is None


# --- endpoint ---

def test_endpoint_found(client):
    r = client.get("/api/boat-library", params={"name": "Diana", "maker": "", "model": ""})
    assert r.status_code == 200
    data = r.json()
    assert data["found"] is True
    assert data["fields"]["boat_maker"] == "Bavaria"
    assert data["fields"]["draft_m"] == 1.95


def test_endpoint_not_found(client):
    r = client.get("/api/boat-library", params={"name": "Black Pearl"})
    assert r.status_code == 200
    assert r.json() == {"found": False}
