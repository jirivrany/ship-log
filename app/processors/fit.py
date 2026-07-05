import re
from datetime import datetime, timezone
from typing import Optional

import fitparse

from app.processors.track import LapPoint, TrackMeta
from app.processors.tz import tz_name_at

SEMICIRCLE_TO_DEG = 180.0 / (2**31)


def _sc(value: int) -> float:
    return value * SEMICIRCLE_TO_DEG


def _ensure_utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def parse_fit_metadata(path: str, filename: str) -> TrackMeta:
    """Extract date, ports, total distance and timezone from FIT file + filename."""
    date, from_port, to_port = _parse_filename(filename)

    fit = fitparse.FitFile(path)
    start_time = None
    total_distance_nm = None
    tz_name = "UTC"

    for msg in fit.get_messages("session"):
        fields = {f.name: f.value for f in msg.fields if f.value is not None}
        st = fields.get("start_time")
        if st:
            start_time = _ensure_utc(st)
            # Date derived from GPS timestamp beats the filename — Strava/Garmin
            # sometimes names the file with the wrong date. We'll convert to
            # local time once tz_name is known; for now store as UTC date as
            # a safe default and overwrite after timezone is resolved.
            date = start_time.strftime("%Y-%m-%d")
        dist_m = fields.get("total_distance")
        if dist_m:
            total_distance_nm = round(dist_m / 1852.0, 2)

        # Derive timezone from session start position
        lat_sc = fields.get("start_position_lat")
        lon_sc = fields.get("start_position_long")
        if lat_sc is not None and lon_sc is not None:
            tz_name = tz_name_at(_sc(lat_sc), _sc(lon_sc))
        break  # single session per file

    # Fallback: use first record position if session had no position
    if tz_name == "UTC":
        for msg in fit.get_messages("record"):
            fields = {f.name: f.value for f in msg.fields if f.value is not None}
            lat_sc = fields.get("position_lat")
            lon_sc = fields.get("position_long")
            if lat_sc is not None and lon_sc is not None:
                tz_name = tz_name_at(_sc(lat_sc), _sc(lon_sc))
                break

    # Re-derive date in local timezone so a leg starting at e.g. 23:30 UTC
    # gets the correct local calendar date, not the UTC date.
    if start_time and tz_name != "UTC":
        from zoneinfo import ZoneInfo
        date = start_time.astimezone(ZoneInfo(tz_name)).strftime("%Y-%m-%d")

    return TrackMeta(
        date=date or "",
        from_port=from_port,
        to_port=to_port,
        total_distance_nm=total_distance_nm,
        start_time=start_time,
        timezone=tz_name,
    )


def parse_fit_laps(path: str) -> list[LapPoint]:
    """Extract manual lap button presses."""
    fit = fitparse.FitFile(path)
    laps: list[LapPoint] = []

    for msg in fit.get_messages("lap"):
        fields = {f.name: f.value for f in msg.fields if f.value is not None}
        if fields.get("lap_trigger") != "manual":
            continue
        ts = fields.get("timestamp")
        # timestamp = moment button was pressed = end of the lap segment,
        # so use end_position for the correct coordinates
        lat_sc = fields.get("end_position_lat")
        lon_sc = fields.get("end_position_long")
        if ts is None or lat_sc is None or lon_sc is None:
            continue
        laps.append(LapPoint(
            timestamp=_ensure_utc(ts),
            lat=_sc(lat_sc),
            lon=_sc(lon_sc),
        ))

    return laps


def _parse_filename(filename: str) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Parse Strava Sauce FIT filename into (date, from_port, to_port).
    Pattern: YYYYMMDD_N_🇭🇷_From_-_To_⛵.fit
    """
    stem = re.sub(r"\.fit$", "", filename, flags=re.IGNORECASE)
    # collapse emoji and non-word chars to underscores, preserve letters+digits
    stem = re.sub(r"[^\w\-]", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")

    m = re.match(r"^(\d{4})(\d{2})(\d{2})_(.+)$", stem)
    if not m:
        return None, None, None

    year, month, day, rest = m.groups()
    date = f"{year}-{month}-{day}"

    # strip leading leg-number segment (digit(s) + underscore)
    rest = re.sub(r"^\d+_", "", rest)

    # split on _-_ (was ' - ' in original name)
    parts = re.split(r"_-_", rest)
    from_port = parts[0].replace("_", " ").strip()
    to_port = parts[-1].replace("_", " ").strip()

    return date, from_port or None, to_port or None
