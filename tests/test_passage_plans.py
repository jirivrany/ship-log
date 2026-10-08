"""Saved passage plans: storage, the leg link, and the two-step workflow.

The workflow these cover is the morning routine — save a plan on the planner,
then attach it to a leg started by hand — so the tests follow that order.
"""
import json
import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool

from app.database import get_session
from app.main import app
from app.models import Leg, PassagePlan, Voyage
from app.passage import Waypoint
from app.passage_plans import (
    PlanError,
    computed_plan,
    create_plan,
    delete_plan,
    parse_waypoints,
    update_plan,
    waypoints_of,
)

UPLOAD_DIR = "/tmp/ship_log_test_uploads"

ZIRJE_TO_VIS = [
    Waypoint(43.66, 15.67, "Žirje"),
    Waypoint(43.61, 15.73, "S of Žirje"),
    Waypoint(43.0626, 16.19, "Vis"),
]


@pytest.fixture(autouse=True)
def setup_upload_dir():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.environ["UPLOAD_DIR"] = UPLOAD_DIR


@pytest.fixture()
def db_session():
    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
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
    voyage = Voyage(name="Chorvatsko 2026", boat_name="Diana")
    db_session.add(voyage)
    db_session.commit()
    db_session.refresh(voyage)
    leg = Leg(voyage_id=voyage.id, from_port="Žirje", to_port="Vis",
              date="2026-06-01", timezone="Europe/Zagreb")
    db_session.add(leg)
    db_session.commit()
    db_session.refresh(leg)
    return leg


# --- waypoints ---

def test_waypoint_names_survive_a_round_trip(db_session):
    """Names are the point: "W of Hvar" beats "3" at the helm."""
    plan = create_plan(db_session, "Žirje → Vis", ZIRJE_TO_VIS)

    assert [w.name for w in waypoints_of(plan)] == [
        "Žirje", "S of Žirje", "Vis"]


def test_a_plan_needs_at_least_two_waypoints():
    with pytest.raises(PlanError):
        parse_waypoints([{"lat": 43.0, "lon": 16.0}])
    with pytest.raises(PlanError):
        parse_waypoints([])


def test_impossible_coordinates_are_refused():
    """Coordinates come from a browser, so they are checked before storage."""
    with pytest.raises(PlanError):
        parse_waypoints([{"lat": 91.0, "lon": 16.0},
                         {"lat": 43.0, "lon": 16.0}])
    with pytest.raises(PlanError):
        parse_waypoints([{"lat": 43.0, "lon": "east"},
                         {"lat": 43.0, "lon": 16.0}])


def test_a_plan_needs_a_name(db_session):
    with pytest.raises(PlanError):
        create_plan(db_session, "   ", ZIRJE_TO_VIS)


def test_unreadable_waypoints_do_not_take_the_page_down(db_session):
    plan = PassagePlan(name="broken", waypoints="{ not json")
    db_session.add(plan)
    db_session.commit()

    assert waypoints_of(plan) == []


# --- courses are derived, never stored ---

def test_courses_are_recomputed_from_the_stored_waypoints(db_session):
    """Storing the numbers too would let them drift from the geometry."""
    plan = create_plan(db_session, "Žirje → Vis", ZIRJE_TO_VIS)

    computed = computed_plan(plan)

    assert len(computed.legs) == 2
    assert computed.total_nm == pytest.approx(42.5, abs=1.0)
    assert computed.legs[0].magnetic_course < computed.legs[0].true_course


# --- editing: link, not copy ---

def test_editing_a_plan_reaches_every_leg_linked_to_it(db_session, leg):
    """The consequence of linking rather than copying — and why the planner
    also offers "save as new"."""
    plan = create_plan(db_session, "Žirje → Vis", ZIRJE_TO_VIS)
    leg.passage_plan_id = plan.id
    db_session.add(leg)
    db_session.commit()

    update_plan(db_session, plan, "Žirje → Vis (revised)",
                ZIRJE_TO_VIS[:2])
    db_session.refresh(leg)

    assert leg.passage_plan.name == "Žirje → Vis (revised)"
    assert len(waypoints_of(leg.passage_plan)) == 2


def test_saving_as_new_leaves_the_original_alone(db_session, leg):
    original = create_plan(db_session, "Žirje → Vis", ZIRJE_TO_VIS)
    leg.passage_plan_id = original.id
    db_session.add(leg)
    db_session.commit()

    copy = create_plan(db_session, "Žirje → Vis (variant)", ZIRJE_TO_VIS[:2])
    db_session.refresh(leg)

    assert copy.id != original.id
    assert leg.passage_plan_id == original.id
    assert len(waypoints_of(original)) == 3


# --- deletion detaches ---

def test_deleting_a_plan_detaches_its_legs(db_session, leg):
    """A dangling link would be worse than none; the sailed track is still the
    record of what happened."""
    plan = create_plan(db_session, "Žirje → Vis", ZIRJE_TO_VIS)
    leg.passage_plan_id = plan.id
    db_session.add(leg)
    db_session.commit()

    detached = delete_plan(db_session, plan)
    db_session.refresh(leg)

    assert detached == 1
    assert leg.passage_plan_id is None


# --- the two-step workflow, over HTTP ---

def test_a_plan_can_be_saved_then_attached_to_a_leg(client, leg, db_session):
    saved = client.post("/passage/plans", json={
        "name": "Žirje → Vis",
        "waypoints": [{"lat": 43.66, "lon": 15.67, "name": "Žirje"},
                      {"lat": 43.0626, "lon": 16.19, "name": "Vis"}],
    }).json()

    client.post(f"/legs/{leg.id}/passage-plan",
                data={"plan_id": str(saved["id"])})
    db_session.refresh(leg)

    assert leg.passage_plan_id == saved["id"]


def test_a_leg_can_detach_its_plan(client, leg, db_session):
    plan = create_plan(db_session, "Žirje → Vis", ZIRJE_TO_VIS)
    leg.passage_plan_id = plan.id
    db_session.add(leg)
    db_session.commit()

    client.post(f"/legs/{leg.id}/passage-plan", data={"plan_id": ""})
    db_session.refresh(leg)

    assert leg.passage_plan_id is None


def test_saving_over_a_plan_updates_it_rather_than_adding_one(client, db_session):
    first = client.post("/passage/plans", json={
        "name": "Žirje → Vis",
        "waypoints": [{"lat": 43.66, "lon": 15.67},
                      {"lat": 43.0626, "lon": 16.19}],
    }).json()

    client.post("/passage/plans", json={
        "id": first["id"],
        "name": "Žirje → Vis (revised)",
        "waypoints": [{"lat": 43.66, "lon": 15.67},
                      {"lat": 43.2, "lon": 16.0}],
    })

    plans = client.get("/passage/plans").json()["plans"]
    assert len(plans) == 1
    assert plans[0]["name"] == "Žirje → Vis (revised)"


def test_saving_a_plan_without_a_name_is_refused(client):
    response = client.post("/passage/plans", json={
        "name": "",
        "waypoints": [{"lat": 43.66, "lon": 15.67},
                      {"lat": 43.0626, "lon": 16.19}],
    })

    assert response.status_code == 422


# --- the leg page ---

def test_leg_page_offers_planning_when_no_plan_is_attached(client, leg):
    html = client.get(f"/legs/{leg.id}").text

    assert "Passage plan" in html
    assert f'/passage?leg={leg.id}' in html


def test_leg_page_shows_the_courses_and_draws_the_planned_route(
        client, leg, db_session):
    plan = create_plan(db_session, "Žirje → Vis", ZIRJE_TO_VIS)
    leg.passage_plan_id = plan.id
    db_session.add(leg)
    db_session.commit()

    html = client.get(f"/legs/{leg.id}").text

    assert "Žirje → Vis" in html
    assert "S of Žirje" in html          # waypoint names reach the helm
    assert "plannedRoute" in html        # second line on the map
    assert f"/passage?plan={plan.id}" in html


def test_planner_can_open_with_a_plan_loaded(client, db_session):
    plan = create_plan(db_session, "Žirje → Vis", ZIRJE_TO_VIS)

    html = client.get(f"/passage?plan={plan.id}").text

    assert "LOADED_WAYPOINTS" in html
    assert "Žirje" in html


def test_the_map_gets_a_view_before_anything_is_drawn_on_it():
    """Leaflet needs a centre and zoom before a layer can be added; adding one
    first throws "Cannot read properties of undefined (reading 'parentNode')"
    inside DomUtil and leaves the map blank. Loading a saved plan and restoring
    stored limit coverage both add layers, so both must follow setView."""
    source = open("app/static/js/passage.js").read()

    set_view = source.index("map.setView")

    assert source.index("LOADED_WAYPOINTS.forEach") > set_view
    assert source.index("  restoreStoredAreas();") > set_view


def test_waypoints_use_the_same_popup_controls_as_leg_map_markers():
    """The two maps should feel the same. On the leg map a marker click opens
    a popup whose actions are buttons; nothing happens on the click itself.
    Waypoints follow that: a click that deleted outright also made naming
    impossible, since the first half of a double-click removed the marker."""
    source = open("app/static/js/passage.js").read()

    assert "waypointPopup" in source
    assert "popup-delete-btn" in source      # the leg map's own action class
    assert "popup-name-btn" in source
    # The marker's click handler must open the popup rather than delete.
    click_handler = source[source.index("marker.on('click'"):]
    click_handler = click_handler[:click_handler.index("marker.on('contextmenu'")]
    assert "openPopup" in click_handler
    assert "removeWaypoint" not in click_handler


def test_planner_remembers_the_leg_it_was_opened_from(client, leg):
    html = client.get(f"/passage?leg={leg.id}").text

    assert f'/legs/{leg.id}' in html
