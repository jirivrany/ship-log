from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.models import AREA_LABELS, AREA_ORDER, UserProfile, Voyage
from app.routers.voyages import gather_voyage_entries
from app.stats import aggregate_stats, compute_stats
from app.templates_env import templates

router = APIRouter()


@router.get("/profile")
def profile_page(request: Request, session: Session = Depends(get_session)):
    profile = session.exec(select(UserProfile)).first()
    voyages = session.exec(select(Voyage).order_by(Voyage.created_at.desc())).all()

    per_voyage = []
    for voyage in voyages:
        legs, leg_entries = gather_voyage_entries(session, voyage)
        stats = compute_stats([e for leg in legs for e in leg_entries[leg.id]])
        per_voyage.append((voyage, stats))

    # Area × role matrix: each voyage falls into exactly one (area, role) cell
    # since was_skipper is a per-voyage flag and area tags the whole voyage.
    # One row per area that has at least one voyage, in canonical open-water order.
    area_rows = []
    for code in AREA_ORDER:
        in_area = [(v, s) for v, s in per_voyage if v.area and v.area.value == code]
        if not in_area:
            continue
        area_rows.append({
            "code": code,
            "label": AREA_LABELS[code],
            "skipper": aggregate_stats([s for v, s in in_area if v.was_skipper]),
            "crew":    aggregate_stats([s for v, s in in_area if not v.was_skipper]),
            "total":   aggregate_stats([s for _, s in in_area]),
        })

    skippered = aggregate_stats([s for v, s in per_voyage if v.was_skipper])
    as_crew = aggregate_stats([s for v, s in per_voyage if not v.was_skipper])
    overall = aggregate_stats([s for _, s in per_voyage])

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "profile": profile,
        "overall": overall,
        "skippered": skippered,
        "as_crew": as_crew,
        "area_rows": area_rows,
    })


@router.post("/profile")
async def save_profile(request: Request, session: Session = Depends(get_session)):
    form = await request.form()
    profile = session.exec(select(UserProfile)).first()
    if not profile:
        profile = UserProfile()
    profile.name = (form.get("name") or "").strip() or None
    session.add(profile)
    session.commit()
    return RedirectResponse("/profile", status_code=303)
