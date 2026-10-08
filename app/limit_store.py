"""Computed offshore-limit areas, persisted so coverage survives a reload.

The coastline cache next door holds raw OSM ways — megabytes per area, keyed
by a hash with no recoverable bounds. What the map needs back is far smaller:
the traced contour and the view it covers, a couple of kilobytes each. Keeping
them in one file lets the planner redraw everything computed so far without
recomputing, and without asking Overpass again.
"""
import json
import os
import tempfile
from typing import Sequence

from app.boundary import ComputedArea

FILENAME = "offshore-limits.json"


def _path(store_dir: str) -> str:
    return os.path.join(store_dir, FILENAME)


def load_areas(store_dir: str) -> list[ComputedArea]:
    """Every area computed so far, or an empty list if there are none."""
    try:
        with open(_path(store_dir)) as handle:
            raw = json.load(handle)
    except (OSError, ValueError):
        # Missing is the normal first-run case; corrupt must not be fatal
        # either, since the areas can always be recomputed.
        return []

    areas = []
    for entry in raw.get("areas", ()):
        try:
            areas.append(ComputedArea.from_dict(entry))
        except (KeyError, TypeError, ValueError):
            continue        # skip a bad entry, keep the good ones
    return areas


def save_areas(store_dir: str, areas: Sequence[ComputedArea]) -> None:
    """Replace the stored set. Written atomically: a crash mid-write would
    otherwise leave a truncated file that loses every computed area."""
    os.makedirs(store_dir, exist_ok=True)
    payload = {"areas": [area.as_dict() for area in areas]}

    handle = tempfile.NamedTemporaryFile(
        "w", dir=store_dir, prefix=FILENAME, suffix=".tmp", delete=False)
    try:
        with handle:
            json.dump(payload, handle)
        os.replace(handle.name, _path(store_dir))
    except OSError:
        try:
            os.unlink(handle.name)
        except OSError:
            pass


def add_area(store_dir: str, area: ComputedArea) -> list[ComputedArea]:
    """Store a newly computed area and return the full set.

    An area that contains one already stored replaces it: the wider contour
    covers the same water, so keeping both would draw the line twice.
    """
    kept = [existing for existing in load_areas(store_dir)
            if not area.covers(existing.south, existing.west,
                               existing.north, existing.east)]
    kept.append(area)
    save_areas(store_dir, kept)
    return kept


def find_covering(areas: Sequence[ComputedArea], south: float, west: float,
                  north: float, east: float) -> ComputedArea | None:
    """An already-computed area containing this view, if there is one."""
    for area in areas:
        if area.covers(south, west, north, east):
            return area
    return None
