"""Strava bundle adapter: transform tests on a minimal bundle, plus tests
against real recorded API responses (tests/fixtures/, see
scripts/record_strava_fixtures.py) once they exist."""
import json
import os
from datetime import datetime, timezone

import pytest

from app.models import TrackSource
from app.processors import loader
from app.processors.strava_track import (
    parse_strava_laps,
    parse_strava_metadata,
    parse_strava_track,
)

FIXTURE_BUNDLE = os.path.join(os.path.dirname(__file__), "fixtures", "strava_bundle.json")

needs_fixture = pytest.mark.skipif(
    not os.path.exists(FIXTURE_BUNDLE),
    reason="recorded Strava fixture not available — run scripts/record_strava_fixtures.py",
)


def _write_bundle(tmp_path, n=12, laps=None, null_fix_at=None, description=None):
    """Minimal bundle: one point every 30 s heading north at ~5 kn."""
    latlng = [[44.0 + i * 0.000694, 15.0] for i in range(n)]
    if null_fix_at is not None:
        latlng[null_fix_at] = None
    bundle = {
        "activity": {
            "id": 999,
            "name": "Sukošan - Ždrelac",
            "start_date": "2026-06-20T08:00:00Z",
            "distance": 5556.0,  # 3.0 Nm
            "description": description,
        },
        "streams": {
            "time": {"data": [i * 30 for i in range(n)]},
            "latlng": {"data": latlng},
            "velocity_smooth": {"data": [2.5722] * n},   # ≈ 5.0 kn
            "distance": {"data": [i * 77.2 for i in range(n)]},
            "temp": {"data": [30] * n},
        },
        "laps": laps or [],
    }
    path = tmp_path / "strava_999.json"
    path.write_text(json.dumps(bundle))
    return str(path)


# --- track transform ---

def test_stream_offsets_become_utc_timestamps(tmp_path):
    track = parse_strava_track(_write_bundle(tmp_path))
    first = track.track_points[0]
    assert first.timestamp == datetime(2026, 6, 20, 8, 0, tzinfo=timezone.utc)
    deltas = [(b.timestamp - a.timestamp).total_seconds()
              for a, b in zip(track.track_points, track.track_points[1:])]
    assert all(d == 30 for d in deltas)


def test_velocity_and_distance_converted(tmp_path):
    track = parse_strava_track(_write_bundle(tmp_path))
    assert all(tp.speed_knots == 5.0 for tp in track.track_points)
    assert all(tp.air_temperature == 30 for tp in track.track_points)
    last = track.track_points[-1]
    assert last.raw_distance_nm == pytest.approx((11 * 77.2) / 1852.0, abs=0.001)


def test_null_gps_fix_skipped(tmp_path):
    track = parse_strava_track(_write_bundle(tmp_path, null_fix_at=3))
    times = [tp.timestamp for tp in track.track_points]
    assert datetime(2026, 6, 20, 8, 1, 30, tzinfo=timezone.utc) not in times
    assert len(times) == 11


def test_metadata_from_activity(tmp_path):
    path = _write_bundle(tmp_path)
    meta = parse_strava_metadata(path, os.path.basename(path))
    assert meta.timezone == "Europe/Zagreb"
    assert meta.date == "2026-06-20"
    assert meta.total_distance_nm == 3.0
    assert meta.from_port == "Sukošan"
    assert meta.to_port == "Ždrelac"
    assert meta.description is None


def test_metadata_description_imported_and_stripped(tmp_path):
    path = _write_bundle(tmp_path, description="  Great sail, force 4 from NW.  ")
    meta = parse_strava_metadata(path, os.path.basename(path))
    assert meta.description == "Great sail, force 4 from NW."


def test_metadata_blank_description_is_none(tmp_path):
    path = _write_bundle(tmp_path, description="   ")
    meta = parse_strava_metadata(path, os.path.basename(path))
    assert meta.description is None


# --- laps ---

def test_last_lap_dropped_and_end_index_resolved(tmp_path):
    laps = [
        {"lap_index": 1, "end_index": 5},
        {"lap_index": 2, "end_index": 11},  # closed by activity stop -> dropped
    ]
    marks = parse_strava_laps(_write_bundle(tmp_path, laps=laps))
    assert len(marks) == 1
    assert marks[0].timestamp == datetime(2026, 6, 20, 8, 2, 30, tzinfo=timezone.utc)
    assert marks[0].lat == pytest.approx(44.0 + 5 * 0.000694)


def test_single_lap_means_no_manual_marks(tmp_path):
    laps = [{"lap_index": 1, "end_index": 11}]
    assert parse_strava_laps(_write_bundle(tmp_path, laps=laps)) == []


# --- loader dispatch ---

def test_loader_routes_json_to_strava(tmp_path):
    path = _write_bundle(tmp_path)
    assert loader.source_for(path) == TrackSource.strava
    assert len(loader.parse_track(path).track_points) == 12
    assert loader.parse_laps(path) == []


def test_loader_rejects_unknown_extension():
    with pytest.raises(ValueError):
        loader.source_for("/tmp/track.tcx")


# --- voyage import window ---

def test_voyage_window_with_margins():
    from datetime import datetime, timezone as tz
    from app.models import Voyage
    from app.routers.strava import _voyage_window

    voyage = Voyage(name="v", boat="b", start_date="2025-10-04", end_date="2025-10-11")
    after, before = _voyage_window(voyage)
    assert after == int(datetime(2025, 10, 3, tzinfo=tz.utc).timestamp())
    assert before == int(datetime(2025, 10, 13, tzinfo=tz.utc).timestamp())


def test_voyage_window_without_dates_is_open():
    from app.models import Voyage
    from app.routers.strava import _voyage_window

    assert _voyage_window(Voyage(name="v", boat="b")) == (None, None)
    after, before = _voyage_window(Voyage(name="v", boat="b", start_date="2025-10-04"))
    assert after is not None and before is None


# --- real recorded fixture ---

@needs_fixture
def test_real_bundle_parses():
    track = parse_strava_track(FIXTURE_BUNDLE)
    assert len(track.track_points) > 10
    times = [tp.timestamp for tp in track.track_points]
    assert times == sorted(times)
    assert all((b - a).total_seconds() >= 30 for a, b in zip(times, times[1:]))
    assert all(tp.speed_knots >= 0 for tp in track.track_points)
    assert track.track_points[-1].distance_nm > 0


@needs_fixture
def test_real_bundle_metadata():
    meta = parse_strava_metadata(FIXTURE_BUNDLE, "strava_bundle.json")
    assert meta.timezone != "UTC"
    assert meta.date
    assert meta.start_time is not None
    assert meta.total_distance_nm and meta.total_distance_nm > 0


@needs_fixture
def test_real_bundle_laps_do_not_crash():
    marks = parse_strava_laps(FIXTURE_BUNDLE)
    assert isinstance(marks, list)
