"""Analysis location validation and CRUD helpers (PRD §5).

Validation is deliberately structural only: coordinates in range, a usable
name, and a real IANA timezone. Whether a point sits on water is the user's
call — ERA5 serves a value for any cell, and the automated signals (distance
moved by the sea-cell walk, resolved elevation) cannot separate a genuine
island point such as Krk from an inland one without rejecting real sailing
areas too.
"""
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from app.models import AnalysisLocation


@dataclass
class LocationInput:
    """Raw, untrusted values straight off the location form."""
    region: str
    name: str
    latitude: Optional[float]
    longitude: Optional[float]
    timezone: str


@dataclass
class ValidationResult:
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_number(raw: str) -> Optional[float]:
    """Float from a form field, tolerating a comma decimal separator."""
    text = (raw or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


# Coordinates are the original caller; thresholds parse identically.
parse_coordinate = parse_number


def validate(data: LocationInput) -> ValidationResult:
    """Structural checks shared by seeding and the add/edit form."""
    errors: dict[str, str] = {}

    if not (data.name or "").strip():
        errors["name"] = "Display name is required"
    if not (data.region or "").strip():
        errors["region"] = "Region is required"

    if data.latitude is None:
        errors["latitude"] = "Latitude is required"
    elif not -90 <= data.latitude <= 90:
        errors["latitude"] = "Latitude must be between -90 and 90"

    if data.longitude is None:
        errors["longitude"] = "Longitude is required"
    elif not -180 <= data.longitude <= 180:
        errors["longitude"] = "Longitude must be between -180 and 180"

    tz = (data.timezone or "").strip()
    if not tz:
        errors["timezone"] = "Timezone is required"
    else:
        try:
            ZoneInfo(tz)
        except Exception:
            errors["timezone"] = f"Unknown IANA timezone: {tz}"

    return ValidationResult(errors=errors)


def slugify(name: str) -> str:
    """"Murtersko More" -> "murtersko_more"; diacritics fold to ASCII."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "_", folded.lower()).strip("_")
    return slug or "location"


def unique_key(session: Session, name: str,
               exclude_id: Optional[int] = None) -> str:
    """A slug not yet taken by another location, suffixed on collision."""
    base = slugify(name)
    candidate, n = base, 1
    while True:
        stmt = select(AnalysisLocation).where(AnalysisLocation.key == candidate)
        if exclude_id is not None:
            stmt = stmt.where(AnalysisLocation.id != exclude_id)
        if session.exec(stmt).first() is None:
            return candidate
        n += 1
        candidate = f"{base}_{n}"


def active_locations(session: Session) -> list[AnalysisLocation]:
    """Selectable locations, ordered for the region-grouped picker."""
    return list(session.exec(
        select(AnalysisLocation)
        .where(AnalysisLocation.active == True)  # noqa: E712 (SQL, not Python)
        .order_by(AnalysisLocation.region, AnalysisLocation.name)
    ).all())


def group_by_region(
    locations: list[AnalysisLocation],
) -> list[tuple[str, list[AnalysisLocation]]]:
    """[(region, [location, ...]), ...] preserving the query's ordering."""
    grouped: dict[str, list[AnalysisLocation]] = {}
    for location in locations:
        grouped.setdefault(location.region, []).append(location)
    return list(grouped.items())


def apply_input(location: AnalysisLocation, data: LocationInput) -> AnalysisLocation:
    """Copy validated values onto a location, clearing stale resolved coords.

    Moving a location invalidates the cell the provider previously served, so
    the recorded resolution is dropped and refilled by the next run.
    """
    moved = (location.latitude != data.latitude
             or location.longitude != data.longitude)
    location.region = data.region.strip()
    location.name = data.name.strip()
    location.latitude = data.latitude
    location.longitude = data.longitude
    location.timezone = data.timezone.strip()
    if moved:
        location.resolved_lat = None
        location.resolved_lon = None
    location.updated_at = datetime.utcnow()
    return location


def record_resolution(session: Session, key: str,
                      lat: float, lon: float) -> None:
    """Remember the cell the provider served, for display in the picker (§5)."""
    location = session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == key)
    ).first()
    if location is None:
        return
    if location.resolved_lat != lat or location.resolved_lon != lon:
        location.resolved_lat = lat
        location.resolved_lon = lon
        session.add(location)
