from datetime import datetime
from typing import Optional

from app.models import EntrySource, LogEntry
from app.processors.fit_track import ProcessedTrack, TrackPoint
from app.processors.fit import LapPoint


def _nearest_track_point(ts: datetime, track: list[TrackPoint]) -> Optional[TrackPoint]:
    if not track:
        return None
    return min(track, key=lambda p: abs((p.timestamp - ts).total_seconds()))


def build_log_entries(
    leg_id: int,
    gpx: ProcessedTrack,
    laps: list[LapPoint],
) -> list[LogEntry]:
    entries: list[LogEntry] = []
    seen_timestamps: set[datetime] = set()

    def add(ts: datetime, lat: float, lon: float, source: EntrySource,
            course: Optional[float], speed: Optional[float],
            log_value: Optional[float], air_temp: Optional[float]):
        # Deduplicate: skip if within 60s of an already-added entry
        for seen in seen_timestamps:
            if abs((ts - seen).total_seconds()) < 60:
                return
        seen_timestamps.add(ts)
        entries.append(LogEntry(
            leg_id=leg_id,
            timestamp=ts,
            lat=lat,
            lon=lon,
            source=source,
            course=course,
            speed=speed,
            log_value=log_value,
            air_temperature=air_temp,
        ))

    # Always include first and last track points as manual anchors
    if gpx.track_points:
        first = gpx.track_points[0]
        last  = gpx.track_points[-1]
        add(first.timestamp, first.lat, first.lon, EntrySource.manual,
            first.course, first.speed_knots, first.distance_nm, first.air_temperature)
        add(last.timestamp, last.lat, last.lon, EntrySource.manual,
            last.course, last.speed_knots, last.distance_nm, last.air_temperature)

    for pt in gpx.turning_points:
        add(pt.timestamp, pt.lat, pt.lon, EntrySource.turning_point,
            pt.course, pt.speed_knots, pt.distance_nm, pt.air_temperature)

    for pt in gpx.hourly_points:
        add(pt.timestamp, pt.lat, pt.lon, EntrySource.hourly,
            pt.course, pt.speed_knots, pt.distance_nm, pt.air_temperature)

    for lap in laps:
        nearest = _nearest_track_point(lap.timestamp, gpx.track_points)
        add(
            lap.timestamp, lap.lat, lap.lon, EntrySource.lap,
            nearest.course if nearest else None,
            nearest.speed_knots if nearest else None,
            nearest.distance_nm if nearest else None,
            nearest.air_temperature if nearest else None,
        )

    entries.sort(key=lambda e: e.timestamp)
    return entries
