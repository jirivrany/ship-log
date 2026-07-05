"""FIT adapter: reads record messages into RawPoints for process_track()."""
from datetime import datetime, timezone

import fitparse

from app.processors.track import (
    KNOTS_PER_MS,
    ProcessedTrack,
    RawPoint,
    process_track,
)

SEMICIRCLE_TO_DEG = 180.0 / (2**31)


def _sc(value: int) -> float:
    return value * SEMICIRCLE_TO_DEG


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def parse_fit_track(path: str) -> ProcessedTrack:
    fit = fitparse.FitFile(path)

    points: list[RawPoint] = []
    for msg in fit.get_messages("record"):
        fields = {f.name: f.value for f in msg.fields if f.value is not None}
        ts = fields.get("timestamp")
        lat_sc = fields.get("position_lat")
        lon_sc = fields.get("position_long")
        if ts is None or lat_sc is None or lon_sc is None:
            continue
        speed_ms = fields.get("enhanced_speed") or fields.get("speed") or 0.0
        points.append(RawPoint(
            timestamp=_ensure_utc(ts),
            lat=_sc(lat_sc),
            lon=_sc(lon_sc),
            speed_knots=speed_ms * KNOTS_PER_MS,
            temp=fields.get("temperature"),
            dist_m=fields.get("distance"),
        ))

    return process_track(points)
