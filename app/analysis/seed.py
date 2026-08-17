"""Preseeded analysis locations (PRD §5).

These are ordinary rows: fully editable and removable. Seeding only inserts a
key that is absent, so it never resurrects a location the user removed, never
overwrites an edit, and never duplicates on restart.
"""
from sqlmodel import Session, select

from app.models import AnalysisLocation

# (key, region, name, latitude, longitude, timezone) — representative sea
# points for distinct sailing areas, not marina positions.
#
# The PRD §5 table also lists Cres (44.95, 14.40). It is omitted: with
# cell_selection=sea it resolves to the same ERA5 cell as Krk (45.0, 14.5), so
# the two would always produce identical statistics. At ERA5's 0.25° resolution
# they are one place, and offering both invites a comparison the data cannot
# support. Add it by hand if a future higher-resolution provider (CERRA) can
# tell them apart.
SEED_LOCATIONS = (
    ("pula",           "Northern Adriatic", "Pula",           44.85, 13.85, "Europe/Zagreb"),
    ("krk",            "Northern Adriatic", "Krk",            45.10, 14.65, "Europe/Zagreb"),
    ("murtersko_more", "Middle Adriatic",   "Murtersko More", 43.82, 15.60, "Europe/Zagreb"),
    ("kornati",        "Middle Adriatic",   "Kornati",        43.78, 15.30, "Europe/Zagreb"),
    ("split",          "Southern Adriatic", "Split",          43.45, 16.35, "Europe/Zagreb"),
    ("hvar",           "Southern Adriatic", "Hvar",           43.15, 16.45, "Europe/Zagreb"),
    ("dubrovnik",      "Southern Adriatic", "Dubrovnik",      42.60, 18.05, "Europe/Zagreb"),
    ("mallorca",       "Balearic Islands",  "Mallorca",       39.70,  3.00, "Europe/Madrid"),
    ("rugen",          "South Baltic",      "Rugen",          54.42, 13.45, "Europe/Berlin"),
    ("falster",        "South Baltic",      "Falster",        54.70, 12.00, "Europe/Copenhagen"),
)


def seed_locations(session: Session) -> int:
    """Insert any missing seed location; returns how many were added."""
    existing = set(session.exec(select(AnalysisLocation.key)).all())
    added = 0
    for key, region, name, lat, lon, tz in SEED_LOCATIONS:
        if key in existing:
            continue
        session.add(AnalysisLocation(
            key=key, region=region, name=name,
            latitude=lat, longitude=lon, timezone=tz,
        ))
        added += 1
    if added:
        session.commit()
    return added
