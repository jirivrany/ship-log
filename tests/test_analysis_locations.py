"""Analysis location validation, slugs, and preseeding (PRD §5)."""
import os

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from app.analysis.locations import (
    LocationInput,
    active_locations,
    apply_input,
    group_by_region,
    parse_coordinate,
    record_resolution,
    slugify,
    unique_key,
    validate,
)
from app.analysis.seed import SEED_LOCATIONS, seed_locations
from app.models import AnalysisLocation


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _input(**overrides) -> LocationInput:
    values = dict(region="Middle Adriatic", name="Murtersko More",
                  latitude=43.82, longitude=15.60, timezone="Europe/Zagreb")
    values.update(overrides)
    return LocationInput(**values)


# --- validation -------------------------------------------------------------

def test_accepts_a_well_formed_location():
    assert validate(_input()).ok


@pytest.mark.parametrize("lat", [-91, 91, 1000])
def test_rejects_latitude_out_of_range(lat):
    assert "latitude" in validate(_input(latitude=lat)).errors


@pytest.mark.parametrize("lon", [-181, 181])
def test_rejects_longitude_out_of_range(lon):
    assert "longitude" in validate(_input(longitude=lon)).errors


@pytest.mark.parametrize("lat,lon", [(-90, -180), (90, 180), (0, 0)])
def test_accepts_coordinate_extremes(lat, lon):
    assert validate(_input(latitude=lat, longitude=lon)).ok


def test_rejects_missing_coordinates():
    errors = validate(_input(latitude=None, longitude=None)).errors
    assert {"latitude", "longitude"} <= errors.keys()


@pytest.mark.parametrize("name", ["", "   "])
def test_rejects_empty_name(name):
    assert "name" in validate(_input(name=name)).errors


def test_rejects_empty_region():
    assert "region" in validate(_input(region="")).errors


def test_rejects_unknown_timezone():
    errors = validate(_input(timezone="Mars/Olympus")).errors
    assert "timezone" in errors
    assert "Mars/Olympus" in errors["timezone"]


def test_rejects_empty_timezone():
    assert "timezone" in validate(_input(timezone="")).errors


def test_an_inland_point_is_accepted():
    """The user decides whether a point is at sea: ERA5 serves every cell and
    no automatic signal separates Krk from Zagreb without rejecting Krk."""
    assert validate(_input(name="Zagreb", latitude=45.81, longitude=15.98)).ok


def test_collects_every_error_at_once():
    errors = validate(_input(name="", latitude=999, timezone="nope")).errors
    assert {"name", "latitude", "timezone"} <= errors.keys()


# --- coordinate parsing -----------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("43.82", 43.82), ("43,82", 43.82), ("  43.82  ", 43.82),
    ("-15.6", -15.6), ("0", 0.0),
])
def test_parses_coordinates_including_comma_decimals(raw, expected):
    assert parse_coordinate(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "north", "43.8.2", None])
def test_unparseable_coordinate_is_none(raw):
    assert parse_coordinate(raw) is None


# --- slugs ------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Murtersko More", "murtersko_more"),
    ("Pula", "pula"),
    ("Šibenik", "sibenik"),          # diacritics fold to ASCII
    ("Rügen", "rugen"),
    ("  Split  ", "split"),
    ("St. Anthony's Bay", "st_anthony_s_bay"),
    ("!!!", "location"),             # nothing usable left
])
def test_slugify(name, expected):
    assert slugify(name) == expected


def test_unique_key_suffixes_on_collision(session):
    session.add(AnalysisLocation(key="split", region="R", name="Split",
                                 latitude=43.45, longitude=16.35,
                                 timezone="Europe/Zagreb"))
    session.commit()

    assert unique_key(session, "Split") == "split_2"


def test_unique_key_ignores_the_row_being_edited(session):
    location = AnalysisLocation(key="split", region="R", name="Split",
                                latitude=43.45, longitude=16.35,
                                timezone="Europe/Zagreb")
    session.add(location)
    session.commit()

    # renaming a location to its own current name keeps its key
    assert unique_key(session, "Split", exclude_id=location.id) == "split"


# --- seeding ----------------------------------------------------------------

def test_seeds_every_prd_location(session):
    added = seed_locations(session)

    assert added == len(SEED_LOCATIONS) == 10
    keys = set(session.exec(select(AnalysisLocation.key)).all())
    assert {"pula", "krk", "murtersko_more", "kornati", "split",
            "hvar", "dubrovnik", "mallorca", "rugen", "falster"} == keys


def test_cres_is_deliberately_not_seeded():
    """PRD §5 lists Cres, but it resolves to the same ERA5 cell as Krk
    (45.0, 14.5), so seeding both would offer a comparison of one place
    against itself."""
    assert "cres" not in {key for key, *_ in SEED_LOCATIONS}


# Sea cells the live API resolved each seed point to, recorded 2026-08-16 with
# cell_selection=sea (scripts/record_weather_analysis_fixtures.py documents the
# request). Naive 0.25° rounding does NOT predict these — the sea-cell walk
# moves points off the plain grid, which is exactly how the Cres/Krk collision
# hid: Krk rounds to 14.75 but actually resolves to 14.5.
RESOLVED_SEED_CELLS = {
    "pula": (44.75, 13.75),
    "krk": (45.0, 14.5),
    "murtersko_more": (43.75, 15.5),
    "kornati": (43.75, 15.25),
    "split": (43.25, 16.25),
    "hvar": (43.25, 16.5),
    "dubrovnik": (42.5, 18.0),
    "mallorca": (40.0, 3.0),
    "rugen": (54.5, 13.5),
    "falster": (54.75, 12.0),
}


def test_no_two_seed_locations_resolve_to_the_same_sea_cell():
    """Two points served by one ERA5 cell always produce identical statistics,
    so comparing them is meaningless. Guards the recorded resolutions; a new
    seed location must be checked against the live API before being added."""
    assert RESOLVED_SEED_CELLS.keys() == {key for key, *_ in SEED_LOCATIONS}

    seen = {}
    for key, cell in RESOLVED_SEED_CELLS.items():
        assert cell not in seen, f"{key} and {seen[cell]} share cell {cell}"
        seen[cell] = key


def test_seeding_is_idempotent(session):
    seed_locations(session)
    assert seed_locations(session) == 0
    assert len(session.exec(select(AnalysisLocation)).all()) == len(SEED_LOCATIONS)


def test_seeding_does_not_resurrect_a_removed_location(session):
    seed_locations(session)
    pula = session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == "pula")
    ).first()
    pula.active = False
    session.add(pula)
    session.commit()

    seed_locations(session)

    pula = session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == "pula")
    ).first()
    assert pula.active is False


def test_seeding_does_not_overwrite_user_edits(session):
    seed_locations(session)
    krk = session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == "krk")
    ).first()
    krk.latitude = 45.00          # nudged seaward by the user
    krk.name = "Krk (west)"
    session.add(krk)
    session.commit()

    seed_locations(session)

    krk = session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == "krk")
    ).first()
    assert (krk.latitude, krk.name) == (45.00, "Krk (west)")


def test_seed_locations_all_pass_validation():
    for key, region, name, lat, lon, tz in SEED_LOCATIONS:
        result = validate(LocationInput(region=region, name=name, latitude=lat,
                                        longitude=lon, timezone=tz))
        assert result.ok, f"{key}: {result.errors}"


# --- picker helpers ---------------------------------------------------------

def test_active_locations_excludes_removed_ones(session):
    seed_locations(session)
    pula = session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == "pula")
    ).first()
    pula.active = False
    session.add(pula)
    session.commit()

    keys = [loc.key for loc in active_locations(session)]
    assert "pula" not in keys
    assert len(keys) == len(SEED_LOCATIONS) - 1


def test_locations_group_by_region_in_order(session):
    seed_locations(session)

    grouped = group_by_region(active_locations(session))

    regions = [region for region, _ in grouped]
    assert regions == sorted(regions)          # query orders by region
    by_region = dict(grouped)
    assert [loc.name for loc in by_region["South Baltic"]] == ["Falster", "Rugen"]


# --- edits ------------------------------------------------------------------

def test_moving_a_location_clears_its_resolved_cell(session):
    location = AnalysisLocation(key="krk", region="Northern Adriatic", name="Krk",
                                latitude=45.10, longitude=14.65,
                                timezone="Europe/Zagreb",
                                resolved_lat=45.0, resolved_lon=14.5)

    apply_input(location, _input(name="Krk", region="Northern Adriatic",
                                 latitude=45.00, longitude=14.60))

    assert location.resolved_lat is None
    assert location.resolved_lon is None


def test_renaming_without_moving_keeps_the_resolved_cell(session):
    location = AnalysisLocation(key="krk", region="Northern Adriatic", name="Krk",
                                latitude=45.10, longitude=14.65,
                                timezone="Europe/Zagreb",
                                resolved_lat=45.0, resolved_lon=14.5)

    apply_input(location, _input(name="Krk (west)", region="Northern Adriatic",
                                 latitude=45.10, longitude=14.65))

    assert (location.resolved_lat, location.resolved_lon) == (45.0, 14.5)
    assert location.name == "Krk (west)"


def test_record_resolution_stores_the_served_cell(session):
    seed_locations(session)

    record_resolution(session, "murtersko_more", 43.75, 15.5)
    session.commit()

    location = session.exec(
        select(AnalysisLocation).where(AnalysisLocation.key == "murtersko_more")
    ).first()
    assert (location.resolved_lat, location.resolved_lon) == (43.75, 15.5)


def test_record_resolution_ignores_an_unknown_key(session):
    record_resolution(session, "atlantis", 0.0, 0.0)  # must not raise


# --- startup path -----------------------------------------------------------

def test_init_db_creates_the_tables_and_seeds_once(tmp_path):
    """init_db must register the models itself: create_all only builds tables
    that have been imported, and database.py imports none of them otherwise.

    Runs in a fresh interpreter on purpose. In-process the assertion is
    worthless, because any earlier test importing app.models has already
    populated SQLModel's metadata and would mask a missing import.
    """
    import subprocess
    import sys

    script = f"""
import os
import app.database as database
database.os.makedirs = lambda *a, **k: None
database.init_db()
database.init_db()          # a restart must not duplicate the seed
from sqlmodel import Session, select
from app.models import AnalysisLocation
with Session(database.engine) as s:
    print(len(s.exec(select(AnalysisLocation)).all()))
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env={**os.environ, "DATABASE_URL": f"sqlite:///{tmp_path}/init.db"},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == str(len(SEED_LOCATIONS))
