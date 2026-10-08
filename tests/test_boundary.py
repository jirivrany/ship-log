"""The 20 NM offshore limit: distance field, contour tracing, and the endpoint.

Geometry is checked against shapes whose answer is known analytically — a
straight coast, a circular island — rather than against real coastline, so a
failure points at the algorithm instead of at OSM data having changed.
"""
import json
import math
import os
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.boundary import MAX_SEARCH_NM, NM_PER_DEGREE_LAT, _SegmentIndex, offshore_limit
from app.coastline import BoundingBox, CoastlineError, fetch_coastline
from sqlmodel import SQLModel, Session, create_engine
from sqlalchemy.pool import StaticPool

from app.database import get_session
from app.main import app


def lon_scale(lat: float) -> float:
    return NM_PER_DEGREE_LAT * math.cos(math.radians(lat))


def circle(lat: float, lon: float, radius_deg: float, points: int = 36):
    """A closed ring, as an island would be."""
    return [(lat + radius_deg * math.cos(2 * math.pi * i / points),
             lon + radius_deg * math.sin(2 * math.pi * i / points))
            for i in range(points + 1)]


@pytest.fixture()
def store_dir(tmp_path):
    """Point the limit store at a scratch directory: these tests must not see
    coverage computed by a real run, nor leave any behind."""
    with patch("app.routers.passage._limit_store_dir",
               return_value=str(tmp_path)):
        yield tmp_path


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
def client(store_dir, db_session):
    app.dependency_overrides[get_session] = lambda: db_session
    with patch("app.main.init_db"), patch("app.main.os.makedirs"):
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
    app.dependency_overrides.clear()


# --- distance ---

def test_distance_to_a_straight_coast_is_exact():
    coast = [[(42.0, 16.0), (44.0, 16.0)]]      # a meridian of coastline
    index = _SegmentIndex(coast, reference_lat=43.0)

    for offset_nm in (5.0, 10.0, 20.0, 30.0):
        lon = 16.0 - offset_nm / lon_scale(43.0)
        assert index.distance_nm(43.0, lon) == pytest.approx(offset_nm, abs=0.01)


def test_distance_saturates_at_the_cap():
    """Far offshore the exact distance is irrelevant and scanning for it is
    what makes an uncapped search slow."""
    coast = [[(42.0, 16.0), (44.0, 16.0)]]
    index = _SegmentIndex(coast, reference_lat=43.0)

    far = 16.0 - 200.0 / lon_scale(43.0)

    assert index.distance_nm(43.0, far) == MAX_SEARCH_NM
    assert index.distance_nm(43.0, far, cap_nm=10.0) == 10.0


# --- contour ---

def test_limit_line_follows_a_straight_coast_at_the_right_distance():
    coast = [[(42.0, 16.0), (44.0, 16.0)]]

    contour = offshore_limit(coast, south=42.2, west=15.2, north=43.8,
                             east=16.1, limit_nm=20.0, resolution_nm=1.0)

    assert len(contour.lines) == 1
    expected_lon = 16.0 - 20.0 / lon_scale(43.0)
    for line in contour.lines:
        for _, lon in line:
            assert lon == pytest.approx(expected_lon, abs=0.01)


def test_an_island_carries_its_own_zone():
    """The whole point of the feature: islands count, not just the mainland."""
    island = circle(43.0, 16.0, 0.02)

    contour = offshore_limit([island], south=42.4, west=15.3, north=43.6,
                             east=16.7, limit_nm=20.0, resolution_nm=1.5)

    assert len(contour.lines) == 1        # one ring around the island
    distances = [
        math.hypot((lat - 43.0) * NM_PER_DEGREE_LAT,
                   (lon - 16.0) * lon_scale(43.0))
        for line in contour.lines for lat, lon in line
    ]
    # Ring radius is the limit plus the island's own ~1.2 NM radius.
    assert min(distances) == pytest.approx(21.2, abs=1.0)
    assert max(distances) == pytest.approx(21.2, abs=1.0)


def test_zones_of_nearby_islands_merge():
    """Two islands 30 NM apart: under a 20 NM limit the water between them is
    covered, so the zones join into one. This is the reasoning that lets a
    limited licence reach an offshore island."""
    gap_deg = 30.0 / lon_scale(43.0)
    west_island = circle(43.0, 16.0 - gap_deg / 2, 0.02)
    east_island = circle(43.0, 16.0 + gap_deg / 2, 0.02)
    box = dict(south=42.3, west=15.0, north=43.7, east=17.0, resolution_nm=1.5)

    merged = offshore_limit([west_island, east_island], limit_nm=20.0, **box)
    separate = offshore_limit([west_island, east_island], limit_nm=5.0, **box)

    assert len(merged.lines) == 1
    assert len(separate.lines) == 2


def test_no_coastline_means_no_line():
    contour = offshore_limit([], south=42.0, west=15.0, north=43.0, east=16.0)

    assert contour.is_empty


def test_limit_must_be_within_the_search_cap():
    coast = [[(42.0, 16.0), (44.0, 16.0)]]

    with pytest.raises(ValueError):
        offshore_limit(coast, south=42.0, west=15.0, north=43.0, east=16.0,
                       limit_nm=0)
    with pytest.raises(ValueError):
        offshore_limit(coast, south=42.0, west=15.0, north=43.0, east=16.0,
                       limit_nm=MAX_SEARCH_NM + 1)


# --- bounding box ---

def test_bounding_box_rejects_inverted_or_impossible_corners():
    with pytest.raises(ValueError):
        BoundingBox(south=44.0, west=15.0, north=43.0, east=16.0)
    with pytest.raises(ValueError):
        BoundingBox(south=42.0, west=17.0, north=43.0, east=16.0)
    with pytest.raises(ValueError):
        BoundingBox(south=-91.0, west=15.0, north=43.0, east=16.0)


def test_nearby_boxes_share_a_cache_key():
    """A pan of a few metres must reuse the cached coastline rather than ask
    Overpass again."""
    a = BoundingBox(south=42.0, west=15.0, north=43.0, east=16.0)
    b = BoundingBox(south=42.0001, west=15.0001, north=43.0001, east=16.0001)
    far = BoundingBox(south=42.5, west=15.0, north=43.0, east=16.0)

    assert a.cache_key == b.cache_key
    assert a.cache_key != far.cache_key


def test_padding_is_clamped_to_real_coordinates():
    padded = BoundingBox(south=-89.9, west=-179.9, north=89.9,
                         east=179.9).padded(1.0)

    assert padded.south >= -90 and padded.north <= 90
    assert padded.west >= -180 and padded.east <= 180


# --- fetching ---

def test_coastline_is_cached_on_disk(tmp_path):
    """The fair-use policy is the reason this cache exists, so a second call
    must not reach the network at all."""
    bbox = BoundingBox(south=42.0, west=16.0, north=42.1, east=16.1)
    payload = {"elements": [
        {"geometry": [{"lat": 42.0, "lon": 16.0}, {"lat": 42.1, "lon": 16.1}]}]}
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payload)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    first = fetch_coastline(bbox, str(tmp_path), client=client)
    second = fetch_coastline(bbox, str(tmp_path), client=client)

    assert len(calls) == 1
    assert first == second == [[(42.0, 16.0), (42.1, 16.1)]]


def test_busy_overpass_is_retried_then_reported(tmp_path):
    """504 is the public instance's normal answer under load."""
    bbox = BoundingBox(south=42.0, west=16.0, north=42.1, east=16.1)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(504, text="gateway timeout")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with patch("app.coastline.time.sleep"):      # no real backoff in tests
        with pytest.raises(CoastlineError, match="busy"):
            fetch_coastline(bbox, str(tmp_path), client=client)

    assert len(calls) == 3


def test_a_transient_failure_recovers_on_retry(tmp_path):
    bbox = BoundingBox(south=42.0, west=16.0, north=42.1, east=16.1)
    payload = {"elements": [
        {"geometry": [{"lat": 42.0, "lon": 16.0}, {"lat": 42.1, "lon": 16.1}]}]}
    responses = [httpx.Response(504), httpx.Response(200, json=payload)]

    client = httpx.Client(
        transport=httpx.MockTransport(lambda request: responses.pop(0)))
    with patch("app.coastline.time.sleep"):
        ways = fetch_coastline(bbox, str(tmp_path), client=client)

    assert ways == [[(42.0, 16.0), (42.1, 16.1)]]


def test_a_permanent_error_is_not_retried(tmp_path):
    """A 400 means the query is wrong; retrying only loads the server."""
    bbox = BoundingBox(south=42.0, west=16.0, north=42.1, east=16.1)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, text="bad request")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(CoastlineError):
        fetch_coastline(bbox, str(tmp_path), client=client)

    assert len(calls) == 1


def test_fetch_identifies_this_application(tmp_path):
    """The fair-use policy requires a User-Agent that names the caller, and
    warns that library defaults may be blocked without notice."""
    bbox = BoundingBox(south=42.0, west=16.0, north=42.1, east=16.1)
    seen = {}

    def handler(request):
        seen["agent"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json={"elements": []})

    # Exercise the client the module builds for itself, not an injected one.
    real_client = httpx.Client
    with patch("app.coastline.httpx.Client",
               lambda **kwargs: real_client(
                   transport=httpx.MockTransport(handler), **kwargs)):
        fetch_coastline(bbox, str(tmp_path))

    assert "ShipLog" in seen["agent"]


# --- endpoint ---

def test_endpoint_returns_the_limit_line(client, tmp_path):
    coast = [[(42.0, 16.0), (44.0, 16.0)]]

    with patch("app.routers.passage.fetch_coastline", return_value=coast):
        body = client.get("/passage/offshore-limit"
                          "?south=42.2&west=15.2&north=43.8&east=16.1").json()

    assert body["limit_nm"] == 20.0
    assert body["lines"]
    assert "OpenStreetMap" in body["attribution"]


def test_endpoint_refuses_an_area_too_large_to_sample(client):
    response = client.get("/passage/offshore-limit"
                          "?south=30&west=0&north=60&east=30")

    assert response.status_code == 422
    assert "zoom in" in response.json()["detail"].lower()


def test_endpoint_refuses_a_wide_shallow_viewport(client):
    """Cost follows area, not either span on its own. This northern-Italy to
    Zagreb view is only 2° tall but 7° wide, and took 85s end to end before it
    was bounded."""
    response = client.get("/passage/offshore-limit"
                          "?south=44.2058&west=10.1239&north=46.3621&east=17.2321")

    assert response.status_code == 422
    assert "zoom in" in response.json()["detail"].lower()


def test_endpoint_accepts_a_realistic_cruising_viewport(client):
    """The guard must not refuse the views this feature exists for: an open
    crossing like Vis to Vieste."""
    coast = [[(42.0, 16.0), (44.0, 16.0)]]

    with patch("app.routers.passage.fetch_coastline", return_value=coast):
        response = client.get("/passage/offshore-limit"
                              "?south=41.8&west=15.5&north=43.2&east=16.9")

    assert response.status_code == 200


def test_endpoint_reports_a_busy_overpass_rather_than_an_empty_map(client):
    """'The server is busy' and 'there is no coast here' must not look alike."""
    with patch("app.routers.passage.fetch_coastline",
               side_effect=CoastlineError("Overpass is busy (HTTP 504)")):
        response = client.get("/passage/offshore-limit"
                              "?south=42.2&west=15.2&north=43.8&east=16.1")

    assert response.status_code == 503
    assert "busy" in response.json()["detail"]


def test_endpoint_rejects_inverted_bounds(client):
    response = client.get("/passage/offshore-limit"
                          "?south=44&west=15.2&north=43&east=16.1")

    assert response.status_code == 422


def test_page_offers_the_limit_button(client):
    html = client.get("/passage").text

    assert 'id="show-limit"' in html
    assert "20 NM limit" in html


def test_a_computed_area_is_served_back_without_recomputing(client):
    """The whole point of persisting: a second request for water already
    measured must not reach Overpass again."""
    coast = [[(42.0, 16.0), (44.0, 16.0)]]
    view = "?south=42.2&west=15.2&north=43.8&east=16.1"

    with patch("app.routers.passage.fetch_coastline",
               return_value=coast) as fetch:
        first = client.get("/passage/offshore-limit" + view).json()
        second = client.get("/passage/offshore-limit" + view).json()

    assert fetch.call_count == 1
    assert first["cached"] is False
    assert second["cached"] is True
    assert second["lines"] == first["lines"]


def test_stored_areas_are_listed_for_a_reloaded_page(client):
    coast = [[(42.0, 16.0), (44.0, 16.0)]]

    with patch("app.routers.passage.fetch_coastline", return_value=coast):
        client.get("/passage/offshore-limit"
                   "?south=42.2&west=15.2&north=43.8&east=16.1")
    listed = client.get("/passage/offshore-limit/areas").json()

    assert len(listed["areas"]) == 1
    assert listed["areas"][0]["lines"]
    assert "OpenStreetMap" in listed["attribution"]


def test_stored_areas_can_be_forgotten(client):
    coast = [[(42.0, 16.0), (44.0, 16.0)]]

    with patch("app.routers.passage.fetch_coastline", return_value=coast):
        client.get("/passage/offshore-limit"
                   "?south=42.2&west=15.2&north=43.8&east=16.1")
    client.delete("/passage/offshore-limit/areas")
    listed = client.get("/passage/offshore-limit/areas").json()

    assert listed["areas"] == []


def test_page_can_extend_limit_coverage_area_by_area(client):
    """Coverage is built up viewport by viewport into a whole sea, so drawing
    a second area must not replace the first — hence a separate clear."""
    html = client.get("/passage").text

    assert 'id="show-limit"' in html
    assert 'id="clear-limit"' in html
    assert 'id="toggle-limit"' in html
    # The button always computes; "show" invited clicking on drawn coverage.
    assert "Compute 20 NM limit" in html


def test_page_explains_the_limit_line_and_the_seamark_purple(client):
    """OpenSeaMap renders traffic separation schemes in violet close to the
    coast, which reads as a second limit line unless it is named."""
    html = client.get("/passage").text

    assert 'id="limit-legend"' in html
    assert "traffic" in html.lower() and "separation" in html.lower()
