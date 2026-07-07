"""Write policy for the leg forecast prefill: which fetched values may land where.

Kept free of I/O so the rules that protect the skipper's own text can be
tested in isolation (mirrors weather_apply for log entries).
"""
from app.forecast import LegForecast
from app.models import Leg

FORECAST_SOURCE = "open-meteo"

# Leg column -> LegForecast attribute (same names by design)
FORECAST_FIELDS = ("sunrise", "sunset", "forecast")


def apply_forecast(leg: Leg, fc: LegForecast, overwrite: bool) -> bool:
    """Write fetched values onto the leg; returns whether anything was written."""
    wrote = False
    for field in FORECAST_FIELDS:
        value = getattr(fc, field)
        if value is None:
            continue
        if getattr(leg, field) is None or overwrite:
            setattr(leg, field, value)
            wrote = True
    if wrote:
        leg.forecast_source = FORECAST_SOURCE
    return wrote
