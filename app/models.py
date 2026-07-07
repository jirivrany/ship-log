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


class NavArea(str, Enum):
    """Navigation area for a whole voyage (CZ/EU skipper-licensing scheme)."""
    area_a = "A"   # Ocean / Cat I — unrestricted
    area_b = "B"   # Sea / Cat II — offshore ≤200 Nm
    area_c = "C"   # Coastal / Cat III — ≤20 Nm
    area_2 = "2"   # Inland/coastal waters ~2 Nm
    area_1 = "1"   # Inland waterways


# Display order (most open water first) and human labels for the summary/form.
AREA_ORDER = ["A", "B", "C", "2", "1"]
AREA_LABELS = {
    "A": "Area A — Ocean",
    "B": "Area B — Sea",
    "C": "Area C — Coastal",
    "2": "Area 2 — Inland/coastal",
    "1": "Area 1 — Inland",
}


class EntrySource(str, Enum):
    turning_point = "turning_point"
    lap = "lap"
    hourly = "hourly"
    manual = "manual"
    quick_note = "quick_note"


class Voyage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str                               # voyage name e.g. "Chorvatsko 2026"
    start_date: Optional[str] = None        # ISO date YYYY-MM-DD (charter start)
    end_date: Optional[str] = None          # ISO date YYYY-MM-DD (charter end)

    # Boat identification (PDF: front page header)
    boat_name: str                              # Jméno jachty, e.g. "Diana"
    boat_maker: Optional[str] = None            # Výrobce, e.g. "Bavaria"
    boat_model: Optional[str] = None            # Typ, e.g. "Cruiser 37"
    year_built: Optional[int] = None            # Rok výroby
    registration_number: Optional[str] = None   # Registrační číslo
    home_port: Optional[str] = None             # Domovský přístav
    call_sign: Optional[str] = None             # Volací značka
    owner: Optional[str] = None                 # Vlastník
    skipper: Optional[str] = None               # Kapitán
    was_skipper: bool = Field(default=False)    # the app user was the skipper
    crew: Optional[str] = None                  # crew names
    area: Optional[NavArea] = None              # navigation area for the whole voyage

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

    @property
    def boat_label(self) -> str:
        """Display string, e.g. 'Diana — Bavaria Cruiser 37 (2016)'."""
        maker_model = " ".join(p for p in (self.boat_maker, self.boat_model) if p)
        label = self.boat_name
        if maker_model:
            label = f"{label} — {maker_model}" if label else maker_model
        if self.year_built:
            label += f" ({self.year_built})"
        return label


class UserProfile(SQLModel, table=True):
    """Single-row table: the app user's profile (single-user app)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: Optional[str] = None


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

    # manually filled (or prefilled by the weather fetch — see weather_source)
    propulsion: PropulsionType = Field(default=PropulsionType.motor)
    wind_direction: Optional[str] = None  # e.g. "NW" or "315"
    wind_force: Optional[int] = None      # Beaufort
    wind_speed_kn: Optional[float] = None  # exact wind speed, knots
    sea_state: Optional[int] = None       # Beaufort
    visibility: Optional[str] = None
    cloud_cover: Optional[int] = None     # oktas 0-8
    atmospheric_pressure: Optional[float] = None
    air_temperature: Optional[float] = None  # pre-filled from Garmin if available
    weather_source: Optional[str] = None  # "open-meteo" when auto-filled; None = observed
    notes: Optional[str] = None

    leg: Optional[Leg] = Relationship(back_populates="log_entries")
