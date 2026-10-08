"""Saved passage plans: storage and the waypoint <-> JSON boundary.

The planner measures a route; this keeps it. Only waypoints are persisted —
courses, distances and variation come back from app.passage on every render,
so what the helm reads can never disagree with the geometry it was drawn from.
"""
import json
from datetime import datetime
from typing import Optional, Sequence

from sqlmodel import Session, select

from app.models import Leg, PassagePlan
from app.passage import PassagePlan as ComputedPlan, Waypoint, build_plan


class PlanError(ValueError):
    """A plan that cannot be saved as given."""


# Two points is the shortest thing that has a course and a distance.
MIN_WAYPOINTS = 2


def parse_waypoints(raw: object) -> list[Waypoint]:
    """Waypoints from decoded JSON, rejecting anything unusable.

    Coordinates arrive from a browser, so they are validated rather than
    trusted: a plan that stores nonsense would fail later at the helm.
    """
    if not isinstance(raw, (list, tuple)):
        raise PlanError("Waypoints must be a list.")

    waypoints = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise PlanError("Each waypoint must be an object.")
        try:
            lat = float(entry["lat"])
            lon = float(entry["lon"])
        except (KeyError, TypeError, ValueError):
            raise PlanError("Each waypoint needs a numeric lat and lon.")
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise PlanError("Waypoint coordinates are out of range.")

        name = entry.get("name")
        name = name.strip() if isinstance(name, str) and name.strip() else None
        waypoints.append(Waypoint(lat=lat, lon=lon, name=name))

    if len(waypoints) < MIN_WAYPOINTS:
        raise PlanError("A plan needs at least two waypoints.")
    return waypoints


def waypoints_to_json(waypoints: Sequence[Waypoint]) -> str:
    return json.dumps([
        {"lat": w.lat, "lon": w.lon, **({"name": w.name} if w.name else {})}
        for w in waypoints
    ])


def waypoints_of(plan: PassagePlan) -> list[Waypoint]:
    """Stored waypoints, or an empty list if the row is unreadable.

    A plan whose JSON cannot be parsed must not take the leg page down; it
    shows as a plan with no route rather than a crash.
    """
    try:
        return parse_waypoints(json.loads(plan.waypoints))
    except (ValueError, TypeError):
        return []


def computed_plan(plan: PassagePlan) -> ComputedPlan:
    """The courses and distances for a stored plan, recomputed now."""
    return build_plan(waypoints_of(plan))


def list_plans(session: Session) -> list[PassagePlan]:
    return list(session.exec(
        select(PassagePlan).order_by(PassagePlan.created_at.desc())).all())


def create_plan(session: Session, name: str,
                waypoints: Sequence[Waypoint],
                notes: Optional[str] = None) -> PassagePlan:
    plan = PassagePlan(name=_clean_name(name),
                       waypoints=waypoints_to_json(waypoints),
                       notes=notes or None)
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def update_plan(session: Session, plan: PassagePlan, name: str,
                waypoints: Sequence[Waypoint],
                notes: Optional[str] = None) -> PassagePlan:
    """Overwrite a plan in place. Every leg linked to it follows, which is the
    point of linking rather than copying — "save as new" is the other button."""
    plan.name = _clean_name(name)
    plan.waypoints = waypoints_to_json(waypoints)
    plan.notes = notes or None
    plan.updated_at = datetime.utcnow()
    session.add(plan)
    session.commit()
    session.refresh(plan)
    return plan


def delete_plan(session: Session, plan: PassagePlan) -> int:
    """Delete a plan, detaching any legs that used it.

    Deleting is allowed even when legs point at it: the sailed track is the
    record of what happened, and a stale link would be worse than none.
    Returns how many legs were detached, so the page can say so.
    """
    legs = list(session.exec(
        select(Leg).where(Leg.passage_plan_id == plan.id)).all())
    for leg in legs:
        leg.passage_plan_id = None
        session.add(leg)
    session.delete(plan)
    session.commit()
    return len(legs)


def _clean_name(name: str) -> str:
    cleaned = (name or "").strip()
    if not cleaned:
        raise PlanError("A plan needs a name.")
    return cleaned[:120]
