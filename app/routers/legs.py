import os
import pathlib
import shutil
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.models import Leg, LogEntry, PropulsionType, Voyage
from app.processors.fit import parse_fit_metadata, parse_fit_laps
from app.processors.fit_track import parse_fit_track
from app.processors.merge import build_log_entries
from app.processors.notes import create_quick_note, filter_note_entries
from app.stats import compute_stats
from app.templates_env import templates

router = APIRouter()

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/data/uploads")


def _save_upload(file: UploadFile, dest_dir: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, file.filename)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return dest


def _generate_gps_entries(
    leg_id: int,
    fit_path: str,
    default_propulsion: str,
    default_wind_direction: Optional[str],
    default_wind_force: Optional[str],
) -> list[LogEntry]:
    """Parse a FIT file and build the GPS-derived LogEntry rows for a leg,
    applying prefill defaults. Does not touch any pre-existing entries on
    the leg — build_log_entries() only knows about the FIT track/laps."""
    track = parse_fit_track(fit_path)
    laps = []
    try:
        laps = parse_fit_laps(fit_path)
    except Exception:
        pass

    prefill_propulsion = PropulsionType(default_propulsion)
    prefill_wind_dir = default_wind_direction.strip() or None if default_wind_direction else None
    prefill_wind_force = int(default_wind_force) if default_wind_force and default_wind_force.strip() else None

    entries = build_log_entries(leg_id, track, laps)
    for entry in entries:
        entry.propulsion = prefill_propulsion
        if prefill_wind_dir:
            entry.wind_direction = prefill_wind_dir
        if prefill_wind_force is not None:
            entry.wind_force = prefill_wind_force

    # A leg almost always starts and ends under motor power (departing/arriving
    # under sail is forbidden in most harbors) — unless the sailor explicitly
    # chose a non-motor propulsion for the whole leg, force the anchor points.
    if entries and prefill_propulsion == PropulsionType.motor:
        entries[0].propulsion = PropulsionType.motor
        entries[-1].propulsion = PropulsionType.motor

    return entries


@router.get("/voyages/{voyage_id}/legs/new", response_class=HTMLResponse)
def new_leg_form(voyage_id: int, request: Request, session: Session = Depends(get_session)):
    voyage = session.get(Voyage, voyage_id)
    if not voyage:
        return HTMLResponse("Not found", status_code=404)
    return templates.TemplateResponse("leg_form.html", {"request": request, "voyage": voyage})


@router.get("/voyages/{voyage_id}/legs/quick/new", response_class=HTMLResponse)
def new_leg_quick_form(voyage_id: int, request: Request, session: Session = Depends(get_session)):
    voyage = session.get(Voyage, voyage_id)
    if not voyage:
        return HTMLResponse("Not found", status_code=404)
    return templates.TemplateResponse("leg_quick_form.html", {"request": request, "voyage": voyage})


@router.post("/voyages/{voyage_id}/legs/quick")
def create_leg_quick(
    voyage_id: int,
    from_port: str = Form(...),
    to_port: str = Form(...),
    date: str = Form(...),
    timezone: str = Form("UTC"),
    session: Session = Depends(get_session),
):
    voyage = session.get(Voyage, voyage_id)
    if not voyage:
        return HTMLResponse("Voyage not found", status_code=404)

    leg = Leg(
        voyage_id=voyage_id,
        from_port=from_port,
        to_port=to_port,
        date=date,
        timezone=timezone,
        fit_path=None,
    )
    session.add(leg)
    session.commit()
    session.refresh(leg)

    return RedirectResponse(f"/legs/{leg.id}", status_code=303)


@router.post("/voyages/{voyage_id}/legs/preview", response_class=HTMLResponse)
async def preview_leg(
    voyage_id: int,
    request: Request,
    fit_file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    """Upload FIT, parse metadata, return pre-filled confirmation form."""
    voyage = session.get(Voyage, voyage_id)
    if not voyage:
        return HTMLResponse("Not found", status_code=404)

    staging_dir = os.path.join(UPLOAD_DIR, "staging", f"voyage_{voyage_id}")
    fit_path = _save_upload(fit_file, staging_dir)
    meta = parse_fit_metadata(fit_path, fit_file.filename)

    return templates.TemplateResponse("leg_confirm.html", {
        "request": request,
        "voyage": voyage,
        "meta": meta,
        "fit_path": fit_path,
        "fit_filename": fit_file.filename,
        "propulsion_types": list(PropulsionType),
    })


@router.post("/voyages/{voyage_id}/legs")
async def create_leg(
    voyage_id: int,
    from_port: str = Form(...),
    to_port: str = Form(...),
    date: str = Form(...),
    timezone: str = Form("UTC"),
    fit_path: str = Form(...),
    # prefill defaults applied to all generated entries
    default_propulsion: str = Form("motor"),
    default_wind_direction: Optional[str] = Form(None),
    default_wind_force: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    voyage = session.get(Voyage, voyage_id)
    if not voyage:
        return HTMLResponse("Voyage not found", status_code=404)

    # Validate fit_path is inside the staging area for this voyage
    staging_dir = pathlib.Path(UPLOAD_DIR, "staging", f"voyage_{voyage_id}").resolve()
    real_fit = pathlib.Path(fit_path).resolve()
    if not str(real_fit).startswith(str(staging_dir) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Validate leg_dir stays inside UPLOAD_DIR (guards against .. in port names)
    upload_root = pathlib.Path(UPLOAD_DIR).resolve()
    leg_dir_path = pathlib.Path(UPLOAD_DIR, f"voyage_{voyage_id}", f"{date}_{from_port}-{to_port}")
    if not leg_dir_path.resolve().is_relative_to(upload_root):
        raise HTTPException(status_code=400, detail="Invalid port name")

    leg_dir = str(leg_dir_path)
    os.makedirs(leg_dir, exist_ok=True)
    filename = os.path.basename(fit_path)
    final_path = os.path.join(leg_dir, filename)
    shutil.move(fit_path, final_path)

    leg = Leg(
        voyage_id=voyage_id,
        from_port=from_port,
        to_port=to_port,
        date=date,
        timezone=timezone,
        fit_path=final_path,
    )
    session.add(leg)
    session.commit()
    session.refresh(leg)

    entries = _generate_gps_entries(
        leg.id, final_path, default_propulsion, default_wind_direction, default_wind_force
    )
    for entry in entries:
        session.add(entry)
    session.commit()

    return RedirectResponse(f"/legs/{leg.id}", status_code=303)


@router.get("/legs/{leg_id}/attach-fit", response_class=HTMLResponse)
def attach_fit_form(leg_id: int, request: Request, session: Session = Depends(get_session)):
    leg = session.get(Leg, leg_id)
    if not leg:
        return HTMLResponse("Not found", status_code=404)
    return templates.TemplateResponse("leg_form.html", {
        "request": request,
        "voyage": leg.voyage,
        "attach_leg": leg,
    })


@router.post("/legs/{leg_id}/attach-fit/preview", response_class=HTMLResponse)
async def attach_fit_preview(
    leg_id: int,
    request: Request,
    fit_file: UploadFile = File(...),
    session: Session = Depends(get_session),
):
    """Upload FIT for an existing (trackless) leg, parse metadata, return
    pre-filled confirmation form."""
    leg = session.get(Leg, leg_id)
    if not leg:
        return HTMLResponse("Not found", status_code=404)

    staging_dir = os.path.join(UPLOAD_DIR, "staging", f"leg_{leg_id}")
    fit_path = _save_upload(fit_file, staging_dir)
    meta = parse_fit_metadata(fit_path, fit_file.filename)

    return templates.TemplateResponse("leg_confirm.html", {
        "request": request,
        "voyage": leg.voyage,
        "meta": meta,
        "fit_path": fit_path,
        "fit_filename": fit_file.filename,
        "propulsion_types": list(PropulsionType),
        "attach_leg": leg,
    })


@router.post("/legs/{leg_id}/attach-fit")
async def attach_fit(
    leg_id: int,
    fit_path: str = Form(...),
    default_propulsion: str = Form("motor"),
    default_wind_direction: Optional[str] = Form(None),
    default_wind_force: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    leg = session.get(Leg, leg_id)
    if not leg:
        return HTMLResponse("Not found", status_code=404)

    # Validate fit_path is inside this leg's staging area
    staging_dir = pathlib.Path(UPLOAD_DIR, "staging", f"leg_{leg_id}").resolve()
    real_fit = pathlib.Path(fit_path).resolve()
    if not str(real_fit).startswith(str(staging_dir) + os.sep):
        raise HTTPException(status_code=400, detail="Invalid file path")

    # Validate leg_dir stays inside UPLOAD_DIR (guards against .. in stored port names)
    upload_root = pathlib.Path(UPLOAD_DIR).resolve()
    leg_dir_path = pathlib.Path(UPLOAD_DIR, f"voyage_{leg.voyage_id}", f"{leg.date}_{leg.from_port}-{leg.to_port}")
    if not leg_dir_path.resolve().is_relative_to(upload_root):
        raise HTTPException(status_code=400, detail="Invalid port name")

    leg_dir = str(leg_dir_path)
    os.makedirs(leg_dir, exist_ok=True)
    filename = os.path.basename(fit_path)
    final_path = os.path.join(leg_dir, filename)
    shutil.move(fit_path, final_path)

    meta = parse_fit_metadata(final_path, filename)
    leg.fit_path = final_path
    leg.timezone = meta.timezone
    session.add(leg)
    session.commit()

    entries = _generate_gps_entries(
        leg.id, final_path, default_propulsion, default_wind_direction, default_wind_force
    )
    for entry in entries:
        session.add(entry)
    session.commit()

    return RedirectResponse(f"/legs/{leg.id}", status_code=303)


@router.get("/legs/{leg_id}", response_class=HTMLResponse)
def leg_detail(leg_id: int, request: Request, session: Session = Depends(get_session)):
    leg = session.get(Leg, leg_id)
    if not leg:
        return HTMLResponse("Not found", status_code=404)

    entries = session.exec(
        select(LogEntry).where(LogEntry.leg_id == leg_id).order_by(LogEntry.timestamp)
    ).all()

    # Convert timestamps to local time for display
    try:
        tz = ZoneInfo(leg.timezone)
    except Exception:
        tz = ZoneInfo("UTC")

    local_entries = []
    for e in entries:
        local_time = e.timestamp.astimezone(tz).strftime("%H:%M")
        local_entries.append((e, local_time))

    track_points = _load_track_points(leg.fit_path) if leg.fit_path else []
    stats = _compute_leg_stats(entries)

    note_entries = filter_note_entries(entries)
    local_notes = []
    for e in note_entries:
        local_time = e.timestamp.astimezone(tz).strftime("%H:%M")
        local_notes.append((e, local_time))

    return templates.TemplateResponse("leg.html", {
        "request": request,
        "leg": leg,
        "voyage": leg.voyage,
        "local_entries": local_entries,
        "local_notes": local_notes,
        "track_points": track_points,
        "tz_name": leg.timezone,
        "stats": stats,
    })


def _compute_leg_stats(entries: list) -> dict:
    return compute_stats(entries)


@router.get("/legs/{leg_id}/quick-note", response_class=HTMLResponse)
def new_quick_note_form(leg_id: int, request: Request, session: Session = Depends(get_session)):
    leg = session.get(Leg, leg_id)
    if not leg:
        return HTMLResponse("Not found", status_code=404)
    return templates.TemplateResponse("quick_note_form.html", {"request": request, "leg": leg})


@router.post("/legs/{leg_id}/quick-note")
def submit_quick_note(
    leg_id: int,
    text: str = Form(...),
    lat: str = Form(""),
    lon: str = Form(""),
    session: Session = Depends(get_session),
):
    leg = session.get(Leg, leg_id)
    if not leg:
        return HTMLResponse("Not found", status_code=404)

    lat_val = float(lat) if lat.strip() else None
    lon_val = float(lon) if lon.strip() else None
    if lat_val is None or lon_val is None:
        # A position is only meaningful as a complete pair — treat a partial
        # fix (e.g. one coordinate missing) the same as no position at all.
        lat_val = lon_val = None

    entry = create_quick_note(leg_id, text.strip(), lat_val, lon_val)
    session.add(entry)
    session.commit()

    return RedirectResponse(f"/legs/{leg_id}", status_code=303)


@router.post("/legs/{leg_id}/entries", response_class=HTMLResponse)
def add_manual_entry(
    leg_id: int,
    request: Request,
    ts: str = Form(...),
    lat: float = Form(...),
    lon: float = Form(...),
    course: float = Form(None),
    speed: float = Form(None),
    dist_nm: float = Form(None),
    temp: float = Form(None),
    session: Session = Depends(get_session),
):
    leg = session.get(Leg, leg_id)
    if not leg:
        return HTMLResponse("Not found", status_code=404)

    from datetime import datetime, timezone as tz_mod
    from app.models import EntrySource
    timestamp = datetime.fromisoformat(ts)
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=tz_mod.utc)
    # Store as naive UTC (consistent with how other entries are stored)
    timestamp_naive = timestamp.astimezone(tz_mod.utc).replace(tzinfo=None)

    # Inherit propulsion/wind from the nearest preceding entry
    all_entries = session.exec(
        select(LogEntry).where(LogEntry.leg_id == leg_id).order_by(LogEntry.timestamp)
    ).all()

    preceding = [e for e in all_entries if e.timestamp <= timestamp_naive]
    inherit = preceding[-1] if preceding else (all_entries[0] if all_entries else None)

    entry = LogEntry(
        leg_id=leg_id,
        timestamp=timestamp_naive,
        lat=lat,
        lon=lon,
        source=EntrySource.manual,
        course=course,
        speed=speed,
        log_value=dist_nm,
        air_temperature=temp,
        propulsion=inherit.propulsion if inherit else PropulsionType.motor,
        wind_direction=inherit.wind_direction if inherit else None,
        wind_force=inherit.wind_force if inherit else None,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)

    try:
        tz = ZoneInfo(leg.timezone)
    except Exception:
        tz = ZoneInfo("UTC")
    local_time = entry.timestamp.replace(tzinfo=tz_mod.utc).astimezone(tz).strftime("%H:%M")

    # Find the entry that should follow this one (for client-side DOM insertion)
    all_entries = session.exec(
        select(LogEntry).where(LogEntry.leg_id == leg_id).order_by(LogEntry.timestamp)
    ).all()
    next_entry = next((e for e in all_entries if e.timestamp > entry.timestamp), None)
    next_id = next_entry.id if next_entry else None

    row_html = templates.TemplateResponse(
        "partials/entry_row.html",
        {"request": request, "entry": entry, "local_time": local_time},
    ).body.decode()

    return HTMLResponse(
        row_html,
        headers={
            "X-Insert-Before": str(next_id) if next_id else "",
            "X-Entry-Id": str(entry.id),
        },
    )


@router.patch("/legs/{leg_id}", response_class=HTMLResponse)
def patch_leg(
    leg_id: int,
    field: str = Form(...),
    value: str = Form(""),
    session: Session = Depends(get_session),
):
    leg = session.get(Leg, leg_id)
    if not leg:
        return HTMLResponse("", status_code=404)
    if field == "from_port":
        leg.from_port = value.strip() or leg.from_port
    elif field == "to_port":
        leg.to_port = value.strip() or leg.to_port
    session.add(leg)
    session.commit()
    return HTMLResponse("")


@router.get("/legs/{leg_id}/summary", response_class=HTMLResponse)
def leg_summary(leg_id: int, request: Request, session: Session = Depends(get_session)):
    entries = session.exec(
        select(LogEntry).where(LogEntry.leg_id == leg_id).order_by(LogEntry.timestamp)
    ).all()
    stats = _compute_leg_stats(entries)
    return templates.TemplateResponse("partials/leg_summary.html", {"request": request, "stats": stats})


@router.delete("/legs/{leg_id}", response_class=HTMLResponse)
def delete_leg(leg_id: int, session: Session = Depends(get_session)):
    leg = session.get(Leg, leg_id)
    if not leg:
        return HTMLResponse("Not found", status_code=404)
    entries = session.exec(select(LogEntry).where(LogEntry.leg_id == leg_id)).all()
    for e in entries:
        session.delete(e)
    session.delete(leg)
    session.commit()
    return HTMLResponse("")


def _load_track_points(fit_path: str) -> list[dict]:
    """Return sampled track points for map rendering and manual entry creation."""
    try:
        track = parse_fit_track(fit_path)
    except Exception:
        return []
    result = []
    for pt in track.track_points:
        result.append({
            "lat": pt.lat,
            "lon": pt.lon,
            "ts": pt.timestamp.isoformat(),
            "speed": pt.speed_knots,
            "cog": pt.course,
            "temp": pt.air_temperature,
            "dist_nm": pt.raw_distance_nm if pt.raw_distance_nm is not None else pt.distance_nm,
        })
    return result
