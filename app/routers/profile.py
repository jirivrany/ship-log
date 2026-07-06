from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select

from app.database import get_session
from app.models import UserProfile, Voyage
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

    return templates.TemplateResponse("profile.html", {
        "request": request,
        "profile": profile,
        "overall": aggregate_stats([s for _, s in per_voyage]),
        "skippered": aggregate_stats([s for v, s in per_voyage if v.was_skipper]),
        "as_crew": aggregate_stats([s for v, s in per_voyage if not v.was_skipper]),
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
