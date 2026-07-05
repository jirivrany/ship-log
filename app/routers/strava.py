"""Strava OAuth flow and on-demand activity import.

Everything is user-initiated: the picker fetches one page of recent
activities, and selecting one downloads its streams/laps into a bundle
file in the staging area — from there the normal leg-confirm flow takes
over, identical to a manual FIT/GPX upload.
"""
import json
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app import strava_api
from app.database import get_session
from app.models import Leg, PropulsionType, Voyage
from app.processors.strava_track import parse_strava_metadata
from app.templates_env import templates

router = APIRouter()

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/data/uploads")


def _safe_next(path: str) -> str:
    # state is echoed back by Strava; only ever redirect within the app
    return path if path.startswith("/") and not path.startswith("//") else "/"


@router.get("/strava/authorize")
def strava_authorize(next: str = "/"):
    try:
        return RedirectResponse(strava_api.authorize_url(state=_safe_next(next)))
    except strava_api.StravaConfigError as e:
        return HTMLResponse(f"<h1>Strava not configured</h1><p>{e}</p>", status_code=500)


@router.get("/strava/callback")
def strava_callback(code: str = "", state: str = "/", error: str = ""):
    if error or not code:
        return HTMLResponse(
            f"<h1>Strava authorization failed</h1><p>{error or 'no code returned'}</p>",
            status_code=400,
        )
    strava_api.exchange_code(code)
    return RedirectResponse(_safe_next(state))


def _voyage_window(voyage: Voyage) -> tuple[int | None, int | None]:
    """Epoch bounds for the picker from the voyage's charter dates.

    Margins are generous (start −1 day, end +2 days) so timezone offsets
    and a late arrival sail can't push an activity outside the window.
    Either bound may be None when the voyage has no dates — then the
    picker falls back to the plain recent timeline.
    """
    def _epoch(date_str: str, days_offset: int) -> int:
        dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return int((dt + timedelta(days=days_offset)).timestamp())

    after = _epoch(voyage.start_date, -1) if voyage.start_date else None
    before = _epoch(voyage.end_date, +2) if voyage.end_date else None
    return after, before


def _activity_rows(session: Session, voyage: Voyage) -> list[dict]:
    after, before = _voyage_window(voyage)
    activities = strava_api.fetch_sail_activities(after=after, before=before)
    ids = [a["id"] for a in activities]
    imported: dict[int, Leg] = {}
    if ids:
        legs = session.exec(select(Leg).where(Leg.strava_activity_id.in_(ids))).all()
        imported = {leg.strava_activity_id: leg for leg in legs}
    return [{
        "id": a["id"],
        "name": a.get("name", ""),
        "date": (a.get("start_date_local") or a.get("start_date") or "")[:10],
        "distance_nm": round(a.get("distance", 0) / 1852.0, 1),
        "imported_leg": imported.get(a["id"]),
    } for a in activities]


def _render_picker(request: Request, session: Session, *, voyage: Voyage,
                   post_base: str, back_url: str, self_url: str,
                   attach_leg: Leg = None):
    try:
        rows = _activity_rows(session, voyage)
    except strava_api.StravaNotAuthorized:
        return RedirectResponse(f"/strava/authorize?next={self_url}")
    except strava_api.StravaConfigError as e:
        return HTMLResponse(f"<h1>Strava not configured</h1><p>{e}</p>", status_code=500)

    return templates.TemplateResponse("strava_activities.html", {
        "request": request,
        "voyage": voyage,
        "attach_leg": attach_leg,
        "rows": rows,
        "post_base": post_base,
        "back_url": back_url,
    })


@router.get("/voyages/{voyage_id}/strava", response_class=HTMLResponse)
def strava_picker_for_voyage(voyage_id: int, request: Request,
                             session: Session = Depends(get_session)):
    voyage = session.get(Voyage, voyage_id)
    if not voyage:
        return HTMLResponse("Not found", status_code=404)
    return _render_picker(
        request, session, voyage=voyage,
        post_base=f"/voyages/{voyage_id}/strava",
        back_url=f"/voyages/{voyage_id}",
        self_url=f"/voyages/{voyage_id}/strava",
    )


@router.get("/legs/{leg_id}/strava", response_class=HTMLResponse)
def strava_picker_for_leg(leg_id: int, request: Request,
                          session: Session = Depends(get_session)):
    leg = session.get(Leg, leg_id)
    if not leg:
        return HTMLResponse("Not found", status_code=404)
    return _render_picker(
        request, session, voyage=leg.voyage, attach_leg=leg,
        post_base=f"/legs/{leg_id}/strava",
        back_url=f"/legs/{leg_id}",
        self_url=f"/legs/{leg_id}/strava",
    )


def _stage_bundle(activity_id: int, staging_dir: str) -> tuple[str, str]:
    """Download the activity bundle into staging; returns (path, filename)."""
    bundle = strava_api.fetch_activity_bundle(activity_id)
    filename = f"strava_{activity_id}.json"
    os.makedirs(staging_dir, exist_ok=True)
    path = os.path.join(staging_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f)
    return path, filename


def _existing_import(session: Session, activity_id: int) -> Leg | None:
    return session.exec(
        select(Leg).where(Leg.strava_activity_id == activity_id)
    ).first()


@router.post("/voyages/{voyage_id}/strava/{activity_id}/preview", response_class=HTMLResponse)
def strava_preview_for_voyage(voyage_id: int, activity_id: int, request: Request,
                              session: Session = Depends(get_session)):
    voyage = session.get(Voyage, voyage_id)
    if not voyage:
        return HTMLResponse("Not found", status_code=404)
    existing = _existing_import(session, activity_id)
    if existing:
        return RedirectResponse(f"/legs/{existing.id}", status_code=303)

    try:
        path, filename = _stage_bundle(
            activity_id, os.path.join(UPLOAD_DIR, "staging", f"voyage_{voyage_id}")
        )
    except strava_api.StravaNotAuthorized:
        return RedirectResponse(f"/strava/authorize?next=/voyages/{voyage_id}/strava",
                                status_code=303)

    meta = parse_strava_metadata(path, filename)
    return templates.TemplateResponse("leg_confirm.html", {
        "request": request,
        "voyage": voyage,
        "meta": meta,
        "track_path": path,
        "track_filename": filename,
        "propulsion_types": list(PropulsionType),
        "strava_activity_id": activity_id,
        "back_url": f"/voyages/{voyage_id}/strava",
    })


@router.post("/legs/{leg_id}/strava/{activity_id}/preview", response_class=HTMLResponse)
def strava_preview_for_leg(leg_id: int, activity_id: int, request: Request,
                           session: Session = Depends(get_session)):
    leg = session.get(Leg, leg_id)
    if not leg:
        return HTMLResponse("Not found", status_code=404)
    existing = _existing_import(session, activity_id)
    if existing:
        return RedirectResponse(f"/legs/{existing.id}", status_code=303)

    try:
        path, filename = _stage_bundle(
            activity_id, os.path.join(UPLOAD_DIR, "staging", f"leg_{leg_id}")
        )
    except strava_api.StravaNotAuthorized:
        return RedirectResponse(f"/strava/authorize?next=/legs/{leg_id}/strava",
                                status_code=303)

    meta = parse_strava_metadata(path, filename)
    return templates.TemplateResponse("leg_confirm.html", {
        "request": request,
        "voyage": leg.voyage,
        "meta": meta,
        "track_path": path,
        "track_filename": filename,
        "propulsion_types": list(PropulsionType),
        "strava_activity_id": activity_id,
        "attach_leg": leg,
        "back_url": f"/legs/{leg_id}/strava",
    })
