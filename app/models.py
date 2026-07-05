from datetime import datetime
from enum import Enum
from typing import Optional
from sqlmodel import Field, Relationship, SQLModel


class PropulsionType(str, Enum):
    motor = "motor"
    sail = "sail"
    both = "both"
    anchor = "anchor"


class TrackSource(str, Enum):
    fit = "fit"
    gpx = "gpx"
    strava = "strava"


class EntrySource(str, Enum):
    turning_point = "turning_point"
    lap = "lap"
    hourly = "hourly"
    manual = "manual"
    quick_note = "quick_note"


class Voyage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str                               # voyage name e.g. "Chorvatsko 2026"

    # Boat identification (PDF: front page header)
    boat: str                               # Jméno/typ jachty
    registration_number: Optional[str] = None   # Registrační číslo
    home_port: Optional[str] = None             # Domovský přístav
    call_sign: Optional[str] = None             # Volací značka
    owner: Optional[str] = None                 # Vlastník
    crew: Optional[str] = None                  # crew names

    # Boat technical specs (PDF: Hlavní údaje o plavidle)
    length_m: Optional[float] = None            # Délka (m)
    beam_m: Optional[float] = None              # Šířka (m)
    draft_m: Optional[float] = None             # Ponor (m)
    air_draft_m: Optional[float] = None         # Výška (m)
    engine_type: Optional[str] = None           # Typ motoru
    engine_power_kw: Optional[float] = None     # Výkon motoru (kW)
    displacement_t: Optional[float] = None      # Výtlak (t)
    max_persons: Optional[int] = None           # Max. počet osob
    sail_area_m2: Optional[float] = None        # Celková plocha plachet (m²)
    mainsail_m2: Optional[float] = None         # Hlavní plachta (m²)
    genoa_m2: Optional[float] = None            # Genua (m²)
    water_tank_l: Optional[int] = None          # Vodní tank (l)
    fuel_tank_l: Optional[int] = None           # Nádrž na naftu (l)

    created_at: datetime = Field(default_factory=datetime.utcnow)

    legs: list["Leg"] = Relationship(back_populates="voyage")


class Leg(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    voyage_id: int = Field(foreign_key="voyage.id")
    from_port: str
    to_port: str
    date: str  # ISO date string YYYY-MM-DD
    timezone: str = "UTC"  # IANA tz name derived from first GPS point
    track_path: Optional[str] = None          # source file on disk (FIT/GPX/Strava stream JSON)
    track_source: Optional[TrackSource] = None
    strava_activity_id: Optional[int] = None  # set for Strava imports; used for duplicate detection

    voyage: Optional[Voyage] = Relationship(back_populates="legs")
    log_entries: list["LogEntry"] = Relationship(back_populates="leg")


class LogEntry(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    leg_id: int = Field(foreign_key="leg.id")

    timestamp: datetime
    lat: Optional[float] = None
    lon: Optional[float] = None
    source: EntrySource
    course: Optional[float] = None       # COG degrees
    speed: Optional[float] = None        # SOG knots
    log_value: Optional[float] = None    # distance Nm from leg start

    # manually filled
    propulsion: PropulsionType = Field(default=PropulsionType.motor)
    wind_direction: Optional[str] = None  # e.g. "NW" or "315"
    wind_force: Optional[int] = None      # Beaufort
    sea_state: Optional[int] = None       # Beaufort
    visibility: Optional[str] = None
    cloud_cover: Optional[int] = None     # oktas 0-8
    atmospheric_pressure: Optional[float] = None
    air_temperature: Optional[float] = None  # pre-filled from Garmin if available
    notes: Optional[str] = None

    leg: Optional[Leg] = Relationship(back_populates="log_entries")
