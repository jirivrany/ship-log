"""Standalone passage planning: measure a route, read off courses and distances.

Independent of voyages, like the weather-analysis page: no voyage selector and
no reads or writes of voyage data. Plans are ephemeral — the map is the input
and the table is the output, and nothing is stored.
"""
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.passage import ATTRIBUTION, magnetic_variation
from app.templates_env import templates

router = APIRouter(prefix="/passage")

# Offered as ETA columns: a cruising yacht's realistic range under sail.
ETA_SPEEDS = (5.0, 6.0, 7.0)


@router.get("", response_class=HTMLResponse)
def passage_page(request: Request):
    """The planner. All measuring happens client-side, so this is a plain page
    with no query parameters to carry."""
    return templates.TemplateResponse(request, "passage.html", {
        "eta_speeds": ETA_SPEEDS,
        "attribution": ATTRIBUTION,
    })


@router.get("/variation")
def variation(lat: float = Query(..., ge=-90, le=90),
              lon: float = Query(..., ge=-180, le=180)):
    """Magnetic variation at a point, east positive.

    The World Magnetic Model lives server-side, so the planner asks for a value
    per leg and caches it. `variation` is null when the model is unavailable;
    the page then shows true courses only rather than failing.
    """
    return {"variation": magnetic_variation(lat, lon)}
