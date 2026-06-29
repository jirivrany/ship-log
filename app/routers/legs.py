import os
import shutil
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.database import get_session
from app.models import Leg, LogEntry, PropulsionType, Voyage
from app.processors.fit import parse_fit_metadata, parse_fit_laps
from app.processors.fit_track import parse_fit_track
from app.processors.merge import build_log_entries

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

UPLOAD_DIR = os.environ.get("UPLOAD_DIR", "/app/data/uploads")


def _save_upload(file: UploadFile, dest_dir: str) -> str:
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, file.filename)
    with open(dest, "wb") as f:
        shutil.copyfileobj(file.file, f)
    return dest


@router.get("/voyages/{voyage_id}/legs/new", response_class=HTMLResponse)
def new_leg_form(voyage_id: int, request: Request, session: Session = Depends(get_session)):
    voyage = session.get(Voyage, voyage_id)
    if not voyage:
        return HTMLResponse("Not found", status_code=404)
    return templates.TemplateResponse("leg_form.html", {"request": request, "voyage": voyage})


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
    default_propulsion: Optional[str] = Form(None),
    default_wind_direction: Optional[str] = Form(None),
    default_wind_force: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    voyage = session.get(Voyage, voyage_id)
    if not voyage:
        return HTMLResponse("Voyage not found", status_code=404)

    leg_dir = os.path.join(UPLOAD_DIR, f"voyage_{voyage_id}", f"{date}_{from_port}-{to_port}")
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

    track = parse_fit_track(final_path)
    laps = []
    try:
        laps = parse_fit_laps(final_path)
    except Exception:
        pass

    # Parse prefill defaults
    prefill_propulsion = PropulsionType(default_propulsion) if default_propulsion else None
    prefill_wind_dir = default_wind_direction.strip() or None if default_wind_direction else None
    prefill_wind_force = int(default_wind_force) if default_wind_force and default_wind_force.strip() else None

    entries = build_log_entries(leg.id, track, laps)
    for entry in entries:
        if prefill_propulsion:
            entry.propulsion = prefill_propulsion
        if prefill_wind_dir:
            entry.wind_direction = prefill_wind_dir
        if prefill_wind_force is not None:
            entry.wind_force = prefill_wind_force
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

    return templates.TemplateResponse("leg.html", {
        "request": request,
        "leg": leg,
        "voyage": leg.voyage,
        "local_entries": local_entries,
        "track_points": track_points,
        "tz_name": leg.timezone,
        "stats": stats,
    })


def _compute_leg_stats(entries: list) -> dict:
    """Compute total / motor / sail / both Nm from consecutive log_value gaps."""
    nm_by_prop: dict[str, float] = {}
    for i in range(1, len(entries)):
        prev, cur = entries[i - 1], entries[i]
        if prev.log_value is None or cur.log_value is None:
            continue
        dist = cur.log_value - prev.log_value
        if dist <= 0:
            continue
        key = prev.propulsion.value if prev.propulsion else "unknown"
        nm_by_prop[key] = nm_by_prop.get(key, 0.0) + dist

    total = sum(nm_by_prop.values())
    return {
        "total_nm": round(total, 1),
        "motor_nm": round(nm_by_prop.get("motor", 0.0), 1),
        "sail_nm": round(nm_by_prop.get("sail", 0.0), 1),
        "both_nm": round(nm_by_prop.get("both", 0.0), 1),
        "entry_count": len(entries),
        "lap_count": sum(1 for e in entries if e.source.value == "lap"),
    }


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
        propulsion=inherit.propulsion if inherit else None,
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
    """Return sampled track points with full metadata for map rendering and manual entry creation."""
    try:
        import fitparse
        import math
        SC = 180.0 / (2**31)
        fit = fitparse.FitFile(fit_path)
        raw = []
        last_ts = None
        for msg in fit.get_messages("record"):
            fields = {f.name: f.value for f in msg.fields if f.value is not None}
            ts = fields.get("timestamp")
            lat_sc = fields.get("position_lat")
            lon_sc = fields.get("position_long")
            if ts is None or lat_sc is None or lon_sc is None:
                continue
            if ts.tzinfo is None:
                from datetime import timezone
                ts = ts.replace(tzinfo=timezone.utc)
            if last_ts is None or (ts - last_ts).total_seconds() >= 30:
                speed_ms = fields.get("enhanced_speed") or fields.get("speed") or 0.0
                raw.append({
                    "ts": ts,
                    "lat": lat_sc * SC,
                    "lon": lon_sc * SC,
                    "speed": round(speed_ms * 1.94384, 2),
                    "temp": fields.get("temperature"),
                    "dist_m": fields.get("distance") or 0.0,
                })
                last_ts = ts

        # Compute COG as bearing to next point (same logic as fit_track.py)
        def _bearing(lat1, lon1, lat2, lon2):
            phi1, phi2 = math.radians(lat1), math.radians(lat2)
            dl = math.radians(lon2 - lon1)
            x = math.sin(dl) * math.cos(phi2)
            y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dl)
            return round((math.degrees(math.atan2(x, y)) + 360) % 360, 1)

        result = []
        for i, pt in enumerate(raw):
            cog = _bearing(pt["lat"], pt["lon"], raw[i+1]["lat"], raw[i+1]["lon"]) if i + 1 < len(raw) else (result[-1]["cog"] if result else None)
            result.append({
                "lat": pt["lat"],
                "lon": pt["lon"],
                "ts": pt["ts"].isoformat(),
                "speed": pt["speed"],
                "cog": cog,
                "temp": pt["temp"],
                "dist_nm": round(pt["dist_m"] / 1852.0, 3),
            })
        return result
    except Exception:
        return []
