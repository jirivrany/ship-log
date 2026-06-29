from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select

from app.database import get_session
from app.models import LogEntry, Voyage

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
def index(request: Request, session: Session = Depends(get_session)):
    voyages = session.exec(select(Voyage).order_by(Voyage.created_at.desc())).all()
    return templates.TemplateResponse("index.html", {"request": request, "voyages": voyages})


@router.post("/voyages", response_class=RedirectResponse)
def create_voyage(
    name: str = Form(...),
    boat: str = Form(...),
    registration_number: str = Form(""),
    home_port: str = Form(""),
    call_sign: str = Form(""),
    owner: str = Form(""),
    crew: str = Form(""),
    length_m: str = Form(""),
    beam_m: str = Form(""),
    draft_m: str = Form(""),
    air_draft_m: str = Form(""),
    engine_type: str = Form(""),
    engine_power_kw: str = Form(""),
    displacement_t: str = Form(""),
    max_persons: str = Form(""),
    sail_area_m2: str = Form(""),
    mainsail_m2: str = Form(""),
    genoa_m2: str = Form(""),
    water_tank_l: str = Form(""),
    fuel_tank_l: str = Form(""),
    session: Session = Depends(get_session),
):
    def _f(v: str) -> Optional[float]:
        return float(v) if v.strip() else None

    def _i(v: str) -> Optional[int]:
        return int(v) if v.strip() else None

    def _s(v: str) -> Optional[str]:
        return v.strip() or None

    voyage = Voyage(
        name=name,
        boat=boat,
        registration_number=_s(registration_number),
        home_port=_s(home_port),
        call_sign=_s(call_sign),
        owner=_s(owner),
        crew=_s(crew),
        length_m=_f(length_m),
        beam_m=_f(beam_m),
        draft_m=_f(draft_m),
        air_draft_m=_f(air_draft_m),
        engine_type=_s(engine_type),
        engine_power_kw=_f(engine_power_kw),
        displacement_t=_f(displacement_t),
        max_persons=_i(max_persons),
        sail_area_m2=_f(sail_area_m2),
        mainsail_m2=_f(mainsail_m2),
        genoa_m2=_f(genoa_m2),
        water_tank_l=_i(water_tank_l),
        fuel_tank_l=_i(fuel_tank_l),
    )
    session.add(voyage)
    session.commit()
    session.refresh(voyage)
    return RedirectResponse(f"/voyages/{voyage.id}", status_code=303)


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

    voyage_stats = {
        "total_nm": round(sum(s["total_nm"] for s in leg_stats.values()), 1),
        "motor_nm": round(sum(s["motor_nm"] for s in leg_stats.values()), 1),
        "sail_nm":  round(sum(s["sail_nm"]  for s in leg_stats.values()), 1),
        "both_nm":  round(sum(s["both_nm"]  for s in leg_stats.values()), 1),
    }

    return templates.TemplateResponse("voyage.html", {
        "request": request,
        "voyage": voyage,
        "legs": legs,
        "leg_stats": leg_stats,
        "voyage_stats": voyage_stats,
    })


def _compute_stats(entries: list) -> dict:
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
        "sail_nm":  round(nm_by_prop.get("sail",  0.0), 1),
        "both_nm":  round(nm_by_prop.get("both",  0.0), 1),
        "entry_count": len(entries),
    }


@router.delete("/voyages/{voyage_id}", response_class=RedirectResponse)
def delete_voyage(voyage_id: int, session: Session = Depends(get_session)):
    voyage = session.get(Voyage, voyage_id)
    if voyage:
        session.delete(voyage)
        session.commit()
    return RedirectResponse("/", status_code=303)
