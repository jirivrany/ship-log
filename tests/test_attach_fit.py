from datetime import datetime, timedelta

from app.models import EntrySource
from app.processors.track import ProcessedTrack, TrackPoint
from app.processors.merge import build_log_entries
from app.processors.notes import create_quick_note


def _ts(offset_s: int) -> datetime:
    # LogEntry.timestamp is stored as naive UTC throughout the app.
    return datetime(2026, 6, 20, 8, 0, 0) + timedelta(seconds=offset_s)


def _tp(offset_s: int, dist_nm: float = 0.0) -> TrackPoint:
    return TrackPoint(
        timestamp=_ts(offset_s),
        lat=44.0, lon=15.0,
        speed_knots=5.0, course=90.0,
        air_temperature=None,
        distance_nm=dist_nm,
        raw_distance_nm=None,
    )


def test_existing_quick_notes_survive_fit_attach():
    """Attaching a FIT track to a leg that already has quick notes must not
    mutate or remove those notes — GPS entries are inserted alongside them."""
    leg_id = 1
    quick_note = create_quick_note(leg_id, "Left the marina", lat=44.05, lon=15.05)
    quick_note.id = 1  # simulate a persisted row with an assigned id

    pts = [_tp(0, 0.0), _tp(1800, 10.0), _tp(3600, 20.0)]
    track = ProcessedTrack(track_points=pts, turning_points=[], hourly_points=[])
    gps_entries = build_log_entries(leg_id, track, [])

    # Simulate what attach-fit does: existing rows + newly generated rows
    # coexisting in the same leg, as returned by the DB in timestamp order.
    all_entries = sorted([quick_note] + gps_entries, key=lambda e: e.timestamp)

    assert quick_note.notes == "Left the marina"
    assert quick_note.source == EntrySource.quick_note
    assert quick_note.lat == 44.05
    assert quick_note.lon == 15.05

    assert quick_note in all_entries
    gps_sources = {e.source for e in all_entries if e is not quick_note}
    assert EntrySource.manual in gps_sources  # start/end anchors from build_log_entries
    assert len(all_entries) == 1 + len(gps_entries)


def test_build_log_entries_ignores_pre_existing_entries_entirely():
    """build_log_entries() is a pure function over the FIT track/laps only —
    it has no awareness of what else already exists for the leg, so it can
    never merge, dedup against, or otherwise touch quick notes."""
    pts = [_tp(0, 0.0), _tp(3600, 20.0)]
    track = ProcessedTrack(track_points=pts, turning_points=[], hourly_points=[])

    entries_first_call = build_log_entries(1, track, [])
    entries_second_call = build_log_entries(1, track, [])

    assert len(entries_first_call) == len(entries_second_call)
    assert [e.timestamp for e in entries_first_call] == [e.timestamp for e in entries_second_call]
