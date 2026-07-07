from datetime import datetime, timezone

from app.models import EntrySource, LogEntry, PropulsionType
from app.processors.notes import create_quick_note, filter_note_entries


def test_create_quick_note_with_position():
    entry = create_quick_note(leg_id=1, text="Briefed crew on MOB", lat=44.1, lon=15.2)
    assert entry.leg_id == 1
    assert entry.notes == "Briefed crew on MOB"
    assert entry.source == EntrySource.quick_note
    assert entry.lat == 44.1
    assert entry.lon == 15.2
    assert entry.timestamp is not None


def test_create_quick_note_without_position():
    entry = create_quick_note(leg_id=1, text="Radio not working", lat=None, lon=None)
    assert entry.lat is None
    assert entry.lon is None
    assert entry.source == EntrySource.quick_note
    assert entry.notes == "Radio not working"


def _entry(notes, source=EntrySource.manual):
    return LogEntry(
        leg_id=1,
        timestamp=datetime(2026, 6, 20, 8, 0, tzinfo=timezone.utc),
        lat=44.0,
        lon=15.0,
        source=source,
        notes=notes,
        propulsion=PropulsionType.motor,
    )


def test_filter_note_entries_excludes_empty_and_none():
    entries = [_entry("has a note"), _entry(None), _entry(""), _entry("another note", EntrySource.quick_note)]
    result = filter_note_entries(entries)
    assert [e.notes for e in result] == ["has a note", "another note"]


def test_filter_note_entries_preserves_order():
    entries = [_entry("first"), _entry(None), _entry("second")]
    result = filter_note_entries(entries)
    assert [e.notes for e in result] == ["first", "second"]


def test_filter_note_entries_empty_input():
    assert filter_note_entries([]) == []
