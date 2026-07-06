import os
import shutil
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app import boat_library
from app.database import get_session
from app.models import Leg, LogEntry, Voyage
from app.processors import loader
from app.stats import compute_stats
from app.templates_env import templates

router = APIRouter()

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/data/uploads")

# Optional voyage form fields and how to parse them; name/boat_name are
# required and handled separately. Shared by create and edit so they cannot
# drift.
_VOYAGE_STR_FIELDS = (
    "start_date", "end_date", "boat_maker", "boat_model",
    "registration_number", "home_port",
    "call_sign", "owner", "skipper", "crew", "engine_type",
)
_VOYAGE_FLOAT_FIELDS = (
    "length_m", "beam_m", "draft_m", "air_draft_m", "engine_power_kw",
    "displacement_t", "sail_area_m2", "mainsail_m2", "genoa_m2",
)
_VOYAGE_INT_FIELDS = ("year_built", "max_persons", "water_tank_l", "fuel_tank_l")


def _apply_voyage_form(voyage: Voyage, form) -> Optional[str]:
    """Copy submitted form values onto the voyage; returns an error message
    if a required field is missing, else None."""
    name = (form.get("name") or "").strip()
    boat_name = (form.get("boat_name") or "").strip()
    if not name or not boat_name:
        return "Voyage name and boat name are required"
    voyage.name = name
    voyage.boat_name = boat_name

    for field in _VOYAGE_STR_FIELDS:
        setattr(voyage, field, (form.get(field) or "").strip() or None)
    for field in _VOYAGE_FLOAT_FIELDS:
        value = (form.get(field) or "").strip()
        setattr(voyage, field, float(value) if value else None)
    for field in _VOYAGE_INT_FIELDS:
        value = (form.get(field) or "").strip()
        setattr(voyage, field, int(value) if value else None)
    return None


@router.get("/api/boat-library")
def boat_library_lookup(name: str = "", maker: str = "", model: str = ""):
    """Prefill data for the voyage form: match by boat name, else maker+model."""
    match = boat_library.lookup(name=name, maker=maker, model=model)
    if not match:
        return {"found": False}
    return {"found": True, **match}


@router.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)):
    voyages = session.exec(select(Voyage).order_by(Voyage.created_at.desc())).all()
    return templates.TemplateResponse("index.html", {"request": request, "voyages": voyages})


@router.post("/voyages")
async def create_voyage(request: Request, session: Session = Depends(get_session)):
    voyage = Voyage(name="", boat_name="")
    error = _apply_voyage_form(voyage, await request.form())
    if error:
        return HTMLResponse(error, status_code=400)
    session.add(voyage)
    session.commit()
    session.refresh(voyage)
    return RedirectResponse(f"/voyages/{voyage.id}", status_code=303)


@router.get("/voyages/{voyage_id}/edit", response_class=HTMLResponse)
def edit_voyage_form(voyage_id: int, request: Request, session: Session = Depends(get_session)):
    voyage = session.get(Voyage, voyage_id)
    if not voyage:
        return HTMLResponse("Not found", status_code=404)
    return templates.TemplateResponse("voyage_edit.html", {"request": request, "v": voyage})


@router.post("/voyages/{voyage_id}/edit")
async def update_voyage(voyage_id: int, request: Request, session: Session = Depends(get_session)):
    voyage = session.get(Voyage, voyage_id)
    if not voyage:
        return HTMLResponse("Not found", status_code=404)
    error = _apply_voyage_form(voyage, await request.form())
    if error:
        return HTMLResponse(error, status_code=400)
    session.add(voyage)
    session.commit()
    return RedirectResponse(f"/voyages/{voyage_id}", status_code=303)


@router.get("/voyages/{voyage_id}", response_class=HTMLResponse)
def voyage_detail(voyage_id: int, request: Request, session: Session = Depends(get_session)):
    voyage = session.get(Voyage, voyage_id)
    if not voyage:
        return HTMLResponse("Not found", status_code=404)
    leg_entries = {}
    for leg in voyage.legs:
        leg_entries[leg.id] = session.exec(
            select(LogEntry).where(LogEntry.leg_id == leg.id).order_by(LogEntry.timestamp)
        ).all()

    def _leg_sort_key(leg):
        entries = leg_entries[leg.id]
        start_ts = entries[0].timestamp if entries else None
        return (leg.date, start_ts)

    legs = sorted(voyage.legs, key=_leg_sort_key)

    leg_stats = {leg.id: _compute_stats(leg_entries[leg.id]) for leg in legs}
    voyage_stats = _compute_stats([e for leg in legs for e in leg_entries[leg.id]])

    leg_tracks = {leg.id: _load_leg_track(leg) for leg in legs}

    return templates.TemplateResponse("voyage.html", {
        "request": request,
        "voyage": voyage,
        "legs": legs,
        "leg_stats": leg_stats,
        "voyage_stats": voyage_stats,
        "leg_tracks": leg_tracks,
    })


def _load_leg_track(leg: Leg) -> list[dict]:
    """Return [[lat, lon], ...] for the full leg track, or [] if no track file."""
    if not leg.track_path:
        return []
    try:
        track = loader.parse_track(leg.track_path)
    except Exception:
        return []
    return [[pt.lat, pt.lon] for pt in track.track_points]


def _compute_stats(entries: list) -> dict:
    return compute_stats(entries)


@router.delete("/voyages/{voyage_id}", response_class=HTMLResponse)
def delete_voyage(voyage_id: int, session: Session = Depends(get_session)):
    """Delete the voyage with everything it owns: legs, their log entries,
    and the uploaded track files. Frees any strava_activity_ids for
    re-import."""
    voyage = session.get(Voyage, voyage_id)
    if voyage:
        for leg in voyage.legs:
            for entry in session.exec(select(LogEntry).where(LogEntry.leg_id == leg.id)).all():
                session.delete(entry)
            session.delete(leg)
        session.delete(voyage)
        session.commit()

        for directory in (
            os.path.join(UPLOAD_DIR, f"voyage_{voyage_id}"),
            os.path.join(UPLOAD_DIR, "staging", f"voyage_{voyage_id}"),
        ):
            shutil.rmtree(directory, ignore_errors=True)

    # htmx caller navigates via HX-Redirect; plain callers get an empty 200
    return HTMLResponse("", headers={"HX-Redirect": "/"})
