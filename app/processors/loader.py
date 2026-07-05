"""Dispatch track-file parsing by format, keyed on file extension."""
import os

from app.models import TrackSource
from app.processors.fit import parse_fit_laps, parse_fit_metadata
from app.processors.fit_track import parse_fit_track
from app.processors.gpx_track import parse_gpx_metadata, parse_gpx_track
from app.processors.strava_track import (
    parse_strava_laps,
    parse_strava_metadata,
    parse_strava_track,
)
from app.processors.track import LapPoint, ProcessedTrack, TrackMeta

# Extensions a user may upload manually; Strava bundles (.json) are only
# ever written by the import flow itself.
TRACK_EXTENSIONS = (".fit", ".gpx")

_SOURCE_BY_EXT = {
    ".fit": TrackSource.fit,
    ".gpx": TrackSource.gpx,
    ".json": TrackSource.strava,
}


def source_for(path: str) -> TrackSource:
    ext = os.path.splitext(path)[1].lower()
    source = _SOURCE_BY_EXT.get(ext)
    if source is None:
        raise ValueError(f"Unsupported track file type: {ext or path}")
    return source


def parse_track(path: str) -> ProcessedTrack:
    source = source_for(path)
    if source == TrackSource.fit:
        return parse_fit_track(path)
    if source == TrackSource.gpx:
        return parse_gpx_track(path)
    return parse_strava_track(path)


def parse_metadata(path: str, filename: str) -> TrackMeta:
    source = source_for(path)
    if source == TrackSource.fit:
        return parse_fit_metadata(path, filename)
    if source == TrackSource.gpx:
        return parse_gpx_metadata(path, filename)
    return parse_strava_metadata(path, filename)


def parse_laps(path: str) -> list[LapPoint]:
    """Manual lap marks — GPX has none."""
    source = source_for(path)
    if source == TrackSource.fit:
        return parse_fit_laps(path)
    if source == TrackSource.strava:
        return parse_strava_laps(path)
    return []
