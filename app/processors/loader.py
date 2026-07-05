"""Dispatch track-file parsing by format, keyed on file extension."""
import os

from app.models import TrackSource
from app.processors.fit import parse_fit_laps, parse_fit_metadata
from app.processors.fit_track import parse_fit_track
from app.processors.gpx_track import parse_gpx_metadata, parse_gpx_track
from app.processors.track import LapPoint, ProcessedTrack, TrackMeta

TRACK_EXTENSIONS = (".fit", ".gpx")


def source_for(path: str) -> TrackSource:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fit":
        return TrackSource.fit
    if ext == ".gpx":
        return TrackSource.gpx
    raise ValueError(f"Unsupported track file type: {ext or path}")


def parse_track(path: str) -> ProcessedTrack:
    if source_for(path) == TrackSource.fit:
        return parse_fit_track(path)
    return parse_gpx_track(path)


def parse_metadata(path: str, filename: str) -> TrackMeta:
    if source_for(path) == TrackSource.fit:
        return parse_fit_metadata(path, filename)
    return parse_gpx_metadata(path, filename)


def parse_laps(path: str) -> list[LapPoint]:
    """Manual lap marks — only FIT carries them."""
    if source_for(path) == TrackSource.fit:
        return parse_fit_laps(path)
    return []
