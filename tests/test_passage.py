"""Passage-plan arithmetic and the planner page.

The rhumb-line values here also pin what app/static/js/passage.js must produce:
the page recomputes the plan client-side while a marker is dragged, so the two
implementations have to agree.
"""
import math
import re
from datetime import date
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool

from app.database import get_session
from app.main import app
from app.routers.passage import ETA_SPEEDS
from app.passage import (
    Waypoint,
    build_plan,
    eta_hours,
    magnetic_variation,
    rhumb_line,
)


@pytest.fixture()
def db_session():
    """The planner page lists saved plans, so it needs a session."""
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


# --- rhumb line ---

def test_due_north_leg():
    d, course = rhumb_line(Waypoint(43.0, 16.0), Waypoint(44.0, 16.0))

    assert course == pytest.approx(0.0)
    # One degree of latitude is 60 nautical miles by definition.
    assert d == pytest.approx(60.0, abs=0.1)


def test_due_south_leg():
    _, course = rhumb_line(Waypoint(44.0, 16.0), Waypoint(43.0, 16.0))

    assert course == pytest.approx(180.0)


def test_east_west_legs_follow_the_parallel():
    """The stretched-latitude term degenerates on an east-west leg; the scale
    is cos(lat) there, not a division by zero."""
    east_d, east_c = rhumb_line(Waypoint(43.0, 16.0), Waypoint(43.0, 17.0))
    west_d, west_c = rhumb_line(Waypoint(43.0, 17.0), Waypoint(43.0, 16.0))

    assert east_c == pytest.approx(90.0)
    assert west_c == pytest.approx(270.0)
    assert east_d == pytest.approx(west_d)
    assert east_d == pytest.approx(60.0 * math.cos(math.radians(43.0)), abs=0.1)


def test_coincident_points_do_not_raise():
    """Clicking twice in one spot is ordinary user behaviour."""
    d, course = rhumb_line(Waypoint(43.0, 16.0), Waypoint(43.0, 16.0))

    assert d == 0.0
    assert course == 0.0


def test_antimeridian_takes_the_short_way():
    d, course = rhumb_line(Waypoint(43.0, 179.9), Waypoint(43.0, -179.9))

    assert course == pytest.approx(90.0)
    assert d < 20        # a short hop, not most of the way round the world


def test_zirje_to_vis_matches_the_chart():
    """The worked example behind this feature."""
    d, course = rhumb_line(Waypoint(43.66, 15.67), Waypoint(43.0626, 16.19))

    assert d == pytest.approx(42.4, abs=0.5)
    assert course == pytest.approx(148, abs=1)


# --- plans ---

def test_plan_accumulates_distance_and_turns():
    plan = build_plan([
        Waypoint(43.66, 15.67, "Žirje"),
        Waypoint(43.61, 15.73, "S of Žirje"),
        Waypoint(43.20, 16.00, "W of Hvar"),
        Waypoint(43.0626, 16.19, "Vis"),
    ])

    assert len(plan.legs) == 3
    assert plan.total_nm == pytest.approx(43.0, abs=0.5)
    assert plan.legs[-1].cumulative_nm == pytest.approx(plan.total_nm)
    # The first leg has nothing to turn from.
    assert plan.legs[0].alter_course is None
    assert plan.legs[1].alter_course == pytest.approx(15, abs=1)   # starboard
    assert plan.legs[2].alter_course == pytest.approx(-20, abs=1)  # port


def test_plan_needs_two_distinct_points():
    assert build_plan([]).is_empty
    assert build_plan([Waypoint(43.0, 16.0)]).is_empty
    # A double click in one place is not a leg.
    assert build_plan([Waypoint(43.0, 16.0), Waypoint(43.0, 16.0)]).is_empty


def test_course_change_takes_the_short_way_round_north():
    """Turning from 350° to 010° is 20° to starboard, not 340° to port."""
    plan = build_plan([
        Waypoint(43.0, 16.0), Waypoint(44.0, 15.8), Waypoint(45.0, 16.1),
    ])

    assert plan.legs[1].alter_course == pytest.approx(
        abs(plan.legs[1].alter_course), abs=180)
    assert -180 <= plan.legs[1].alter_course <= 180


# --- magnetic variation ---

def test_variation_in_croatian_waters():
    """Published declination for Split is about +4.8°E."""
    v = magnetic_variation(43.51, 16.44, on=date(2026, 9, 16))

    assert v == pytest.approx(4.8, abs=0.3)


def test_magnetic_course_is_true_less_easterly_variation():
    plan = build_plan([Waypoint(43.66, 15.67), Waypoint(43.0626, 16.19)],
                      on=date(2026, 9, 16))
    leg = plan.legs[0]

    assert leg.variation is not None
    assert leg.magnetic_course == pytest.approx(
        (leg.true_course - leg.variation) % 360, abs=0.01)
    assert leg.magnetic_course < leg.true_course   # easterly variation


def test_plan_survives_an_unavailable_magnetic_model():
    """True courses are useful on their own, so a model failure blanks the
    magnetic column rather than taking the plan down."""
    with patch("app.passage.magnetic_variation", return_value=None):
        plan = build_plan([Waypoint(43.66, 15.67), Waypoint(43.06, 16.19)])

    assert plan.legs[0].magnetic_course is None
    assert plan.legs[0].true_course > 0


# --- eta ---

def test_eta_scales_with_speed():
    assert eta_hours(43.0, 6.0) == pytest.approx(43.0 / 6.0)
    assert eta_hours(43.0, 0) is None
    assert eta_hours(43.0, -1) is None


# --- page and endpoint ---

def test_magnetic_model_is_actually_installed():
    """The plan degrades to true-courses-only when pygeomag is missing, which
    is the right behaviour but hides a broken deploy: the magnetic column just
    shows dashes. Fail loudly here instead."""
    import importlib

    assert importlib.util.find_spec("pygeomag") is not None, (
        "pygeomag is not installed — the magnetic column will silently blank. "
        "Rebuild the image after a requirements change."
    )
    assert magnetic_variation(43.51, 16.44) is not None


def test_planner_page_renders(client):
    html = client.get("/passage").text

    assert 'id="passage-map"' in html
    assert "/static/js/passage.js" in html
    # both course columns are offered
    assert "True" in html and "Magnetic" in html


def test_planner_page_is_linked_from_the_navbar(client):
    """The navbar comes from base.html, so any page that extends it proves the
    link without needing a database."""
    assert 'href="/passage"' in client.get("/passage").text


def test_planner_page_has_a_place_for_the_variation_note(client):
    """The magnetic column is only trustworthy if the correction behind it is
    stated, so the page carries an element for it."""
    html = client.get("/passage").text

    assert 'id="variation-note"' in html


def test_planner_page_carries_the_model_attribution(client):
    html = client.get("/passage").text

    assert "World Magnetic Model" in html


def test_variation_endpoint_returns_a_value(client):
    body = client.get("/passage/variation?lat=43.51&lon=16.44").json()

    assert body["variation"] == pytest.approx(4.8, abs=0.3)


def test_eta_cells_are_addressable_by_their_rendered_attribute(client):
    """The page fills ETA cells by querying [data-eta-speed]. Jinja renders a
    float as "5.0" while JavaScript stringifies 5.0 as "5", so a selector built
    from the number silently matches nothing and every ETA stays blank."""
    html = client.get("/passage").text

    rendered = re.findall(r'data-eta-speed="([^"]+)"', html)
    assert rendered, "no ETA cells rendered"
    # Whatever the formatting, the page must parse back to the real speeds.
    assert [float(value) for value in rendered] == [float(s) for s in ETA_SPEEDS]


def test_variation_endpoint_rejects_impossible_coordinates(client):
    assert client.get("/passage/variation?lat=91&lon=16").status_code == 422
    assert client.get("/passage/variation?lat=43&lon=200").status_code == 422
