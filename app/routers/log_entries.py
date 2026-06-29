from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session
from typing import Optional

from app.database import get_session
from app.models import LogEntry, PropulsionType

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

FIELD_SETTERS = {
    "propulsion":           lambda e, v: setattr(e, "propulsion", PropulsionType(v) if v else None),
    "wind_direction":       lambda e, v: setattr(e, "wind_direction", v or None),
    "wind_force":           lambda e, v: setattr(e, "wind_force", int(v) if v else None),
    "sea_state":            lambda e, v: setattr(e, "sea_state", int(v) if v else None),
    "atmospheric_pressure": lambda e, v: setattr(e, "atmospheric_pressure", float(v) if v else None),
    "air_temperature":      lambda e, v: setattr(e, "air_temperature", float(v) if v else None),
    "notes":                lambda e, v: setattr(e, "notes", v or None),
}


@router.patch("/entries/{entry_id}", response_class=HTMLResponse)
def patch_entry(
    entry_id: int,
    request: Request,
    field: str = Form(...),
    value: str = Form(""),
    session: Session = Depends(get_session),
):
    """Save a single field from an on-blur HTMX patch."""
    entry = session.get(LogEntry, entry_id)
    if not entry:
        return HTMLResponse("", status_code=404)

    setter = FIELD_SETTERS.get(field)
    if setter:
        try:
            setter(entry, value.strip())
        except (ValueError, KeyError):
            pass  # invalid value — ignore, keep existing

    session.add(entry)
    session.commit()

    # Return empty 200 — the field stays in place, no DOM swap needed
    return HTMLResponse("", status_code=200)


@router.delete("/entries/{entry_id}", response_class=HTMLResponse)
def delete_entry(entry_id: int, session: Session = Depends(get_session)):
    entry = session.get(LogEntry, entry_id)
    if not entry:
        return HTMLResponse("", status_code=404)
    session.delete(entry)
    session.commit()
    # Return empty string — HTMX outerHTML swap removes the row
    return HTMLResponse("")
