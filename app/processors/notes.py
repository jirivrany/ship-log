from datetime import datetime
from typing import Optional

from app.models import EntrySource, LogEntry


def create_quick_note(
    leg_id: int, text: str, lat: Optional[float], lon: Optional[float],
    timestamp: Optional[datetime] = None,
) -> LogEntry:
    """Build a quick-note LogEntry. Caller is responsible for persisting it."""
    return LogEntry(
        leg_id=leg_id,
        timestamp=timestamp or datetime.utcnow(),
        lat=lat,
        lon=lon,
        source=EntrySource.quick_note,
        notes=text,
    )


def filter_note_entries(entries: list[LogEntry]) -> list[LogEntry]:
    """Return only entries with non-empty notes, preserving input order."""
    return [e for e in entries if e.notes]
