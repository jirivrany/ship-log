"""Shared timezone lookup from GPS coordinates."""
from timezonefinder import TimezoneFinder

_tf = TimezoneFinder()


def tz_name_at(lat: float, lon: float) -> str:
    """IANA timezone name for a position, falling back to UTC."""
    return _tf.timezone_at(lat=lat, lng=lon) or "UTC"
