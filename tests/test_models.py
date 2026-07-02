from datetime import datetime, timezone

from app.models import EntrySource, LogEntry


def test_log_entry_accepts_null_lat_lon():
    entry = LogEntry(
        leg_id=1,
        timestamp=datetime(2026, 6, 20, 8, 0, tzinfo=timezone.utc),
        lat=None,
        lon=None,
        source=EntrySource.quick_note,
        notes="No position available",
    )
    assert entry.lat is None
    assert entry.lon is None


def test_quick_note_is_valid_entry_source():
    assert EntrySource("quick_note") == EntrySource.quick_note
