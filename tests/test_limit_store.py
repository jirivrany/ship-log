"""Persisted offshore-limit coverage.

Computed contours are kept so a reloaded page redraws what the app has already
measured, instead of spending another Overpass fetch on the same water.
"""
import json
import os

import pytest

from app.boundary import ComputedArea
from app.limit_store import (
    FILENAME,
    add_area,
    find_covering,
    load_areas,
    save_areas,
)


def area(south=42.0, west=15.0, north=44.0, east=17.0, lines=None):
    return ComputedArea(south=south, west=west, north=north, east=east,
                        limit_nm=20.0,
                        lines=lines if lines is not None
                        else [[(43.0, 16.0), (43.1, 16.1)]])


def test_nothing_stored_yet_is_not_an_error(tmp_path):
    assert load_areas(str(tmp_path)) == []


def test_an_area_survives_a_round_trip(tmp_path):
    add_area(str(tmp_path), area())

    restored = load_areas(str(tmp_path))

    assert len(restored) == 1
    assert restored[0].lines == [[(43.0, 16.0), (43.1, 16.1)]]
    assert restored[0].limit_nm == 20.0


def test_areas_accumulate(tmp_path):
    add_area(str(tmp_path), area(south=42.0, west=15.0, north=44.0, east=17.0))
    add_area(str(tmp_path), area(south=44.5, west=12.5, north=45.6, east=14.0))

    assert len(load_areas(str(tmp_path))) == 2


def test_a_wider_area_replaces_the_one_it_contains(tmp_path):
    """The wider contour already covers that water; keeping both would draw
    the line twice."""
    add_area(str(tmp_path), area(south=43.0, west=16.0, north=43.5, east=16.5))
    add_area(str(tmp_path), area(south=42.0, west=15.0, north=44.0, east=17.0))

    stored = load_areas(str(tmp_path))

    assert len(stored) == 1
    assert stored[0].south == 42.0


def test_an_empty_contour_is_still_remembered(tmp_path):
    """"Everywhere here is within the limit" is a real answer, and recomputing
    it would cost another fetch to learn the same thing."""
    add_area(str(tmp_path), area(lines=[]))

    stored = load_areas(str(tmp_path))

    assert len(stored) == 1
    assert stored[0].lines == []


def test_a_corrupt_store_does_not_take_the_page_down(tmp_path):
    (tmp_path / FILENAME).write_text("{ not json")

    assert load_areas(str(tmp_path)) == []


def test_a_bad_entry_is_skipped_but_good_ones_survive(tmp_path):
    (tmp_path / FILENAME).write_text(json.dumps({"areas": [
        {"south": "nonsense"},
        {"south": 42.0, "west": 15.0, "north": 44.0, "east": 17.0,
         "limit_nm": 20.0, "lines": [[[43.0, 16.0], [43.1, 16.1]]]},
    ]}))

    stored = load_areas(str(tmp_path))

    assert len(stored) == 1
    assert stored[0].south == 42.0


def test_saving_does_not_leave_a_partial_file_behind(tmp_path):
    """The write is atomic, so a crash cannot truncate the coverage away."""
    save_areas(str(tmp_path), [area()])

    leftovers = [name for name in os.listdir(tmp_path)
                 if name.endswith(".tmp")]

    assert leftovers == []
    assert len(load_areas(str(tmp_path))) == 1


# --- coverage lookup ---

def test_a_view_inside_a_computed_area_is_covered():
    areas = [area(south=42.0, west=15.0, north=44.0, east=17.0)]

    assert find_covering(areas, 42.5, 15.5, 43.5, 16.5) is not None
    assert find_covering(areas, 42.0, 15.0, 44.0, 17.0) is not None


def test_a_view_reaching_beyond_a_computed_area_is_not_covered():
    """Zooming out or panning to new water has more coastline to measure."""
    areas = [area(south=42.0, west=15.0, north=44.0, east=17.0)]

    assert find_covering(areas, 41.0, 14.0, 45.0, 18.0) is None   # zoomed out
    assert find_covering(areas, 44.5, 15.0, 45.5, 17.0) is None   # panned away
    assert find_covering(areas, 43.0, 16.0, 45.0, 18.0) is None   # overlapping
