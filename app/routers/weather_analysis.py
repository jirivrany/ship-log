"""Standalone historical weather analysis (PRD: features/weather_analysis.md).

Independent of voyages: no voyage selector, no voyage links, and no reads or
writes of voyage data.
"""
from datetime import date

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlmodel import Session

from app.analysis import charts, exports, locations as locations_mod
from app.analysis.aggregate import Thresholds
from app.analysis.provider import ProviderError
from app.analysis.runner import (
    ATTRIBUTION,
    AnalysisRequest,
    AnalysisResult,
    delete_run,
    load_result,
    location_label,
    recent_runs,
    run_analysis,
    snapshots_of,
)
from app.analysis.window import WindowError, default_year_range, describe
from app.database import get_session
from app.models import AnalysisLocation
from app.templates_env import templates

router = APIRouter(prefix="/weather-analysis")

# PRD §6: the first offered scenario.
DEFAULT_WINDOW = {"start_month": 10, "start_day": 24,
                  "end_month": 10, "end_day": 30}


def _form_context(session: Session, request: Request, **extra) -> dict:
    first_year, last_year = default_year_range()
    context = {
        "request": request,
        "grouped_locations": locations_mod.group_by_region(
            locations_mod.active_locations(session)),
        "defaults": {**DEFAULT_WINDOW,
                     "first_year": first_year, "last_year": last_year},
        "thresholds": Thresholds(),
        # Paired with their location labels: runs over the same window and
        # years are told apart by which places they compared.
        "runs": [(run, location_label(run)) for run in recent_runs(session)],
        "attribution": ATTRIBUTION,
        "describe": describe,
        "current_year": date.today().year,
    }
    context.update(extra)
    return context


def _result_context(request: Request, result: AnalysisResult) -> dict:
    per_location = {}
    for summary in result.ranked:
        days = result.daily.get(summary.location_key, [])
        per_location[summary.location_key] = {
            "summary": summary,
            "days": days,
            "rose": charts.wind_rose(summary.statistics["direction_distribution"]),
            "wind_bars": charts.wind_histogram(summary.statistics),
            "rain_bars": charts.rainfall_histogram(days),
            "rating_bars": charts.rating_bars(summary.statistics),
        }
    years = sorted({d.window_year
                    for days in result.daily.values() for d in days})
    return {
        "request": request,
        "result": result,
        "run": result.run,
        "ranked": result.ranked,
        "per_location": per_location,
        "snapshots": {s["key"]: s for s in snapshots_of(result.run)},
        "years": years,
        "window_label": describe(result.run.start_month, result.run.start_day,
                                 result.run.end_month, result.run.end_day),
        "attribution": ATTRIBUTION,
        "rating_colors": charts.RATING_COLORS,
    }


@router.get("", response_class=HTMLResponse)
def analysis_page(request: Request, session: Session = Depends(get_session)):
    return templates.TemplateResponse(
        "weather_analysis/index.html", _form_context(session, request))


def _int(form, name: str, fallback: int) -> int:
    try:
        return int((form.get(name) or "").strip())
    except ValueError:
        return fallback


@router.post("/run")
async def start_analysis(request: Request,
                         session: Session = Depends(get_session)):
    form = await request.form()
    location_ids = [int(v) for v in form.getlist("location_ids") if v.isdigit()]

    thresholds = Thresholds.from_dict({
        field: value for field in Thresholds.__dataclass_fields__
        if (value := locations_mod.parse_number(form.get(field) or "")) is not None
    })
    analysis_request = AnalysisRequest(
        location_ids=location_ids,
        start_month=_int(form, "start_month", DEFAULT_WINDOW["start_month"]),
        start_day=_int(form, "start_day", DEFAULT_WINDOW["start_day"]),
        end_month=_int(form, "end_month", DEFAULT_WINDOW["end_month"]),
        end_day=_int(form, "end_day", DEFAULT_WINDOW["end_day"]),
        first_year=_int(form, "first_year", default_year_range()[0]),
        last_year=_int(form, "last_year", default_year_range()[1]),
        thresholds=thresholds,
    )

    try:
        result = run_analysis(session, analysis_request)
    except (WindowError, ProviderError) as exc:
        # PRD §9: a failure is an explicit error state, never a partial result.
        return templates.TemplateResponse(
            "weather_analysis/index.html",
            _form_context(session, request, error=str(exc),
                          selected_ids=location_ids),
            status_code=400,
        )
    return RedirectResponse(f"/weather-analysis/runs/{result.run.id}",
                            status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def run_page(run_id: int, request: Request,
             session: Session = Depends(get_session)):
    result = load_result(session, run_id)
    if result is None:
        return HTMLResponse("Analysis run not found", status_code=404)
    return templates.TemplateResponse(
        "weather_analysis/result.html", _result_context(request, result))


@router.post("/runs/{run_id}/delete")
def remove_run(run_id: int, session: Session = Depends(get_session)):
    delete_run(session, run_id)
    return RedirectResponse("/weather-analysis", status_code=303)


# --- exports ----------------------------------------------------------------

def _attachment(body: str, filename: str, media_type: str) -> Response:
    return Response(
        content=body,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/runs/{run_id}/export/daily.csv")
def export_daily(run_id: int, session: Session = Depends(get_session)):
    result = load_result(session, run_id)
    if result is None:
        return HTMLResponse("Analysis run not found", status_code=404)
    return _attachment(exports.daily_csv(result),
                       exports.export_filename(result, "daily", "csv"),
                       "text/csv")


@router.get("/runs/{run_id}/export/hourly.csv")
def export_hourly(run_id: int, session: Session = Depends(get_session)):
    result = load_result(session, run_id)
    if result is None:
        return HTMLResponse("Analysis run not found", status_code=404)
    return _attachment(exports.hourly_csv(session, result),
                       exports.export_filename(result, "hourly", "csv"),
                       "text/csv")


@router.get("/runs/{run_id}/export/summary.json")
def export_summary(run_id: int, session: Session = Depends(get_session)):
    result = load_result(session, run_id)
    if result is None:
        return HTMLResponse("Analysis run not found", status_code=404)
    return _attachment(exports.summary_json(result),
                       exports.export_filename(result, "summary", "json"),
                       "application/json")


# --- location management (PRD §5) -------------------------------------------

def _locations_partial(request: Request, session: Session, **extra) -> Response:
    context = {
        "request": request,
        "grouped_locations": locations_mod.group_by_region(
            locations_mod.active_locations(session)),
    }
    context.update(extra)
    return templates.TemplateResponse(
        "weather_analysis/partials/location_list.html", context)


@router.get("/locations", response_class=HTMLResponse)
def list_locations(request: Request, editing_id: int = 0,
                   session: Session = Depends(get_session)):
    return _locations_partial(request, session,
                              editing_id=editing_id or None)


def _location_input(form) -> locations_mod.LocationInput:
    return locations_mod.LocationInput(
        region=(form.get("region") or "").strip(),
        name=(form.get("name") or "").strip(),
        latitude=locations_mod.parse_coordinate(form.get("latitude") or ""),
        longitude=locations_mod.parse_coordinate(form.get("longitude") or ""),
        timezone=(form.get("timezone") or "").strip(),
    )


@router.post("/locations", response_class=HTMLResponse)
async def create_location(request: Request,
                          session: Session = Depends(get_session)):
    form = await request.form()
    data = _location_input(form)
    validation = locations_mod.validate(data)
    if not validation.ok:
        return _locations_partial(request, session, errors=validation.errors,
                                  submitted=data)

    location = AnalysisLocation(
        key=locations_mod.unique_key(session, data.name),
        region=data.region, name=data.name,
        latitude=data.latitude, longitude=data.longitude,
        timezone=data.timezone,
    )
    session.add(location)
    session.commit()
    return _locations_partial(request, session)


@router.post("/locations/{location_id}", response_class=HTMLResponse)
async def update_location(location_id: int, request: Request,
                          session: Session = Depends(get_session)):
    location = session.get(AnalysisLocation, location_id)
    if location is None:
        return HTMLResponse("Location not found", status_code=404)

    form = await request.form()
    data = _location_input(form)
    validation = locations_mod.validate(data)
    if not validation.ok:
        return _locations_partial(request, session, errors=validation.errors,
                                  submitted=data, editing_id=location_id)

    locations_mod.apply_input(location, data)
    session.add(location)
    session.commit()
    return _locations_partial(request, session)


@router.delete("/locations/{location_id}", response_class=HTMLResponse)
def remove_location(location_id: int, request: Request,
                    session: Session = Depends(get_session)):
    """Soft removal: prior runs keep their snapshots (PRD §5)."""
    location = session.get(AnalysisLocation, location_id)
    if location is not None:
        location.active = False
        session.add(location)
        session.commit()
    return _locations_partial(request, session)
