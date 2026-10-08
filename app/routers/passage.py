"""Standalone passage planning: measure a route, read off courses and distances.

Independent of voyages, like the weather-analysis page: no voyage selector and
no reads or writes of voyage data. Plans are ephemeral — the map is the input
and the table is the output, and nothing is stored.
"""
import math
import os
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlmodel import Session

from app.boundary import ComputedArea, offshore_limit
from app.coastline import (
    ATTRIBUTION as COASTLINE_ATTRIBUTION,
    BoundingBox,
    CoastlineError,
    fetch_coastline,
)
from app.limit_store import add_area, find_covering, load_areas, save_areas
from app.database import get_session
from app.models import PassagePlan
from app.passage import ATTRIBUTION, magnetic_variation
from app.passage_plans import (
    PlanError,
    create_plan,
    delete_plan,
    list_plans,
    parse_waypoints,
    update_plan,
    waypoints_of,
)
from app.templates_env import templates

router = APIRouter(prefix="/passage")

# Offered as ETA columns: a cruising yacht's realistic range under sail.
ETA_SPEEDS = (5.0, 6.0, 7.0)

# The skipper licence this was built for. Fixed rather than configurable: one
# limit keeps the cache keyed by area alone.
LIMIT_NM = 20.0

# Coastline beyond the view still shapes the line inside it, so the fetched
# box is grown by a little over the limit.
FETCH_MARGIN_DEG = (LIMIT_NM + 2.0) / 60.0

# Largest viewport we will sample, in square nautical miles. Comfortably covers
# a whole cruising ground (the Vis-Vieste crossing is ~5k, the Split-Vis box
# ~8k) while refusing the continent-wide views that take minutes.
MAX_AREA_SQ_NM = 15_000.0


def _area_sq_nm(bbox: BoundingBox) -> float:
    mid_lat = math.radians((bbox.south + bbox.north) / 2)
    height = (bbox.north - bbox.south) * 60.0
    width = (bbox.east - bbox.west) * 60.0 * math.cos(mid_lat)
    return height * width


def _limit_store_dir() -> str:
    """Computed contours live beside the coastline cache they were traced
    from, both being derived reference data rather than user uploads."""
    uploads = os.environ.get("UPLOAD_DIR", "/app/data/uploads")
    return os.path.dirname(uploads.rstrip("/"))


def _coastline_cache_dir() -> str:
    """Cached coastline lives beside the uploads, not inside them: uploads are
    per-leg track files, and this is shared reference data."""
    uploads = os.environ.get("UPLOAD_DIR", "/app/data/uploads")
    return os.path.join(os.path.dirname(uploads.rstrip("/")), "coastline")


@router.get("", response_class=HTMLResponse)
def passage_page(request: Request,
                 plan: Optional[int] = Query(None),
                 leg: Optional[int] = Query(None),
                 session: Session = Depends(get_session)):
    """The planner. Measuring happens client-side; the page carries the saved
    plans so one can be loaded, and remembers a leg to link back to when the
    planner was opened from a leg page."""
    loaded = session.get(PassagePlan, plan) if plan else None
    return templates.TemplateResponse(request, "passage.html", {
        "eta_speeds": ETA_SPEEDS,
        "attribution": ATTRIBUTION,
        "plans": list_plans(session),
        "loaded_plan": loaded,
        "loaded_waypoints": [
            {"lat": w.lat, "lon": w.lon, "name": w.name}
            for w in (waypoints_of(loaded) if loaded else [])
        ],
        "return_leg_id": leg,
    })


@router.get("/plans")
def plans_json(session: Session = Depends(get_session)):
    """Saved plans, for the planner's load picker."""
    return {"plans": [
        {"id": p.id, "name": p.name,
         "waypoints": [{"lat": w.lat, "lon": w.lon, "name": w.name}
                       for w in waypoints_of(p)]}
        for p in list_plans(session)
    ]}


@router.post("/plans")
def save_plan(payload: dict,
              session: Session = Depends(get_session)):
    """Save a new plan, or overwrite an existing one when given its id.

    "Save as new" simply omits the id — that is how a plan attached to several
    legs gets edited without changing what those legs were sailed against.
    """
    try:
        waypoints = parse_waypoints(payload.get("waypoints"))
        name = payload.get("name") or ""
        notes = payload.get("notes")
        plan_id = payload.get("id")
        if plan_id:
            existing = session.get(PassagePlan, int(plan_id))
            if existing is None:
                raise HTTPException(status_code=404, detail="Plan not found.")
            plan = update_plan(session, existing, name, waypoints, notes)
        else:
            plan = create_plan(session, name, waypoints, notes)
    except PlanError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return {"id": plan.id, "name": plan.name}


@router.delete("/plans/{plan_id}")
def remove_plan(plan_id: int, session: Session = Depends(get_session)):
    plan = session.get(PassagePlan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found.")
    detached = delete_plan(session, plan)
    return {"deleted": plan_id, "legs_detached": detached}


@router.get("/variation")
def variation(lat: float = Query(..., ge=-90, le=90),
              lon: float = Query(..., ge=-180, le=180)):
    """Magnetic variation at a point, east positive.

    The World Magnetic Model lives server-side, so the planner asks for a value
    per leg and caches it. `variation` is null when the model is unavailable;
    the page then shows true courses only rather than failing.
    """
    return {"variation": magnetic_variation(lat, lon)}


@router.get("/offshore-limit")
def offshore_limit_lines(
    south: float = Query(..., ge=-90, le=90),
    west: float = Query(..., ge=-180, le=180),
    north: float = Query(..., ge=-90, le=90),
    east: float = Query(..., ge=-180, le=180),
):
    """The 20 NM limit line for a viewport, as polylines of [lat, lon].

    Islands count: the distance is to the nearest coastline of anything, so an
    island 30 miles offshore carries its own 20-mile zone and the two merge.
    """
    try:
        bbox = BoundingBox(south=south, west=west, north=north, east=east)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    # Sampling cost scales with area, not with either span, so a wide, shallow
    # viewport is as expensive as a square one. Measured: ~8k sq NM computes in
    # a couple of seconds, while 39k sq NM took 85s end to end — past any
    # reasonable wait, and past the browser's patience. Bound it by area.
    if _area_sq_nm(bbox) > MAX_AREA_SQ_NM:
        raise HTTPException(
            status_code=422,
            detail="Area is too large to compute quickly — zoom in a little "
                   "and try again.")

    # An area already computed needs no Overpass call and no sampling: the
    # stored contour covers this view and the answer would be identical.
    stored = find_covering(load_areas(_limit_store_dir()),
                           south, west, north, east)
    if stored is not None:
        return {**stored.as_dict(), "cached": True,
                "attribution": COASTLINE_ATTRIBUTION}

    try:
        ways = fetch_coastline(bbox.padded(FETCH_MARGIN_DEG),
                               _coastline_cache_dir())
    except CoastlineError as exc:
        raise HTTPException(status_code=503, detail=str(exc))

    contour = offshore_limit(ways, south=south, west=west,
                             north=north, east=east, limit_nm=LIMIT_NM)

    area = ComputedArea(south=south, west=west, north=north, east=east,
                        limit_nm=contour.limit_nm, lines=contour.lines)
    add_area(_limit_store_dir(), area)

    return {**area.as_dict(), "cached": False,
            "attribution": COASTLINE_ATTRIBUTION}


@router.get("/offshore-limit/areas")
def stored_offshore_limits():
    """Every area computed so far, so a reloaded page can redraw the coverage
    it already has instead of recomputing it."""
    areas = load_areas(_limit_store_dir())
    return {
        "areas": [area.as_dict() for area in areas],
        "attribution": COASTLINE_ATTRIBUTION,
    }


@router.delete("/offshore-limit/areas")
def forget_offshore_limits():
    """Drop the stored coverage. The coastline cache is left alone: it is the
    expensive part to refetch, and it is what makes a recompute quick."""
    save_areas(_limit_store_dir(), [])
    return {"areas": []}
