from datetime import datetime
from enum import Enum
from typing import Optional
from sqlalchemy import Index
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
    engine_power_hp: Optional[float] = None     # Výkon motoru (hp — customary for boat engines)
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


class PassagePlan(SQLModel, table=True):
    """A named route measured in the planner, saved for reuse.

    Only the waypoints are stored. Courses, distances and magnetic variation
    are recomputed from them on every render by app/passage.py, so the numbers
    shown can never drift from the geometry they describe.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    # [{"lat": .., "lon": .., "name": ".."}, ...] in sailing order, as JSON —
    # a waypoint has no identity of its own and is never queried separately.
    waypoints: str
    notes: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: Optional[datetime] = None

    legs: list["Leg"] = Relationship(back_populates="passage_plan")


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
    # Planned route for this leg, drawn alongside the sailed track. A link
    # rather than a copy: the same plan can serve legs sailed more than once.
    passage_plan_id: Optional[int] = Field(default=None,
                                           foreign_key="passageplan.id")

    # Forecast block (PDF: KWS "Předpověď počasí" page) — manually filled,
    # or prefilled by the forecast fetch (see forecast_source)
    synoptic_situation: Optional[str] = None
    forecast: Optional[str] = None            # wind forecast text
    warnings: Optional[str] = None            # official warnings (manual)
    sunrise: Optional[str] = None             # HH:MM in leg-local time
    sunset: Optional[str] = None              # HH:MM in leg-local time
    forecast_source: Optional[str] = None     # "open-meteo" when auto-filled; None = skipper's own
    synoptic_chart_path: Optional[str] = None  # stored ECMWF chart PNG (CC-BY-4.0)

    voyage: Optional[Voyage] = Relationship(back_populates="legs")
    log_entries: list["LogEntry"] = Relationship(back_populates="leg")
    passage_plan: Optional["PassagePlan"] = Relationship(back_populates="legs")


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
    sails: Optional[str] = None           # sails set, e.g. "Main R1 + Genoa"
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


# ---------------------------------------------------------------------------
# Historical weather analysis (PRD: features/weather_analysis.md)
#
# Deliberately shares nothing with Voyage/Leg/LogEntry: the analyzer is a
# standalone planning tool that neither reads nor writes voyage data.
# ---------------------------------------------------------------------------


class SailingRating(str, Enum):
    """Daily suitability class. Red beats yellow beats green (PRD §8).

    Member names deliberately equal their values: SQLModel persists enums by
    member *name*, so this keeps the stored token readable and identical to
    what exports and templates emit. (Contrast NavArea, whose name/value
    differ, which is why it must be read via `.value`.)
    """
    green = "green"
    yellow = "yellow"
    red = "red"


class AnalysisLocation(SQLModel, table=True):
    """A candidate destination: a representative sea point, not a marina."""
    id: Optional[int] = Field(default=None, primary_key=True)
    key: str = Field(index=True, unique=True)  # slug, e.g. "murtersko_more"
    region: str                                # picker grouping, e.g. "Middle Adriatic"
    name: str                                  # display name, e.g. "Murtersko More"
    latitude: float                            # as configured by the user
    longitude: float
    timezone: str                              # IANA name, drives daily grouping

    # Sea-cell probe results (scripts/verify_analysis_locations.py or the add
    # form). NULL verified_at means "not probed yet" — selectable, but flagged.
    resolved_lat: Optional[float] = None       # cell the provider actually serves
    resolved_lon: Optional[float] = None
    verified_at: Optional[datetime] = None

    # Soft removal: runs keep their location snapshots, so a removed location
    # must never be deleted outright (PRD §5).
    active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AnalysisRun(SQLModel, table=True):
    """One asked question: these locations, this window, these thresholds.

    The observations themselves live in HourlyObservationCache and are shared
    across runs; a run stores only its metadata and derived daily summaries.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

    # Recurring annual window; end before start means the end falls in year+1.
    start_month: int
    start_day: int
    end_month: int
    end_day: int
    first_year: int
    last_year: int

    provider: str = Field(default="open-meteo")
    model: str = Field(default="era5")

    # Frozen at run time so a later threshold or location edit cannot silently
    # change what an existing result page and its exports mean.
    thresholds_json: str
    location_snapshots_json: str

    status: str = Field(default="ok")          # "ok" | "error"
    error_message: Optional[str] = None


class DailySummary(SQLModel, table=True):
    """One local calendar day at one location, aggregated from hourly data."""
    id: Optional[int] = Field(default=None, primary_key=True)
    run_id: int = Field(foreign_key="analysisrun.id", index=True)
    location_key: str = Field(index=True)
    local_date: str                            # ISO date YYYY-MM-DD, location-local
    window_year: int                           # year containing the window start

    avg_wind_kn: Optional[float] = None
    max_wind_kn: Optional[float] = None
    max_gust_kn: Optional[float] = None
    rain_total_mm: Optional[float] = None
    dominant_direction: Optional[str] = None   # eight-point sector
    sailing_rating: Optional[SailingRating] = None
    lost_day: bool = Field(default=False)


class HourlyObservationCache(SQLModel, table=True):
    """Normalized provider data for one location-day.

    ERA5 is immutable, so a cached day is valid forever and a repeated request
    must reuse it instead of calling the provider (PRD §7). Keyed by provider
    and model too, so a future Copernicus/CERRA adapter fills its own rows.
    """
    # One composite index serves the only lookup there is and enforces the
    # uniqueness the cache relies on: one row per provider-model-location-day.
    __table_args__ = (
        Index(
            "ix_hourly_cache_key",
            "provider", "model", "location_key", "local_date",
            unique=True,
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str
    model: str
    location_key: str
    local_date: str                            # ISO date, location-local

    payload_json: str                          # list of normalized hourly rows
    resolved_lat: Optional[float] = None
    resolved_lon: Optional[float] = None
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)
