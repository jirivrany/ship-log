"""Historical weather for log entries: unit derivations + Open-Meteo client."""
from dataclasses import dataclass
from typing import Optional


@dataclass
class WeatherObservation:
    """Weather at one log-entry position/hour, in the log's native units."""
    wind_speed_kn: Optional[float] = None
    wind_direction: Optional[str] = None      # 16-point sector, e.g. "SSW"
    wind_force: Optional[int] = None          # Beaufort
    air_temperature: Optional[float] = None   # °C
    atmospheric_pressure: Optional[float] = None  # hPa, mean sea level
    cloud_cover: Optional[int] = None         # oktas 0-8
    sea_state: Optional[int] = None           # Douglas 0-9

# WMO Beaufort scale: exclusive upper wind-speed bound (knots) per force 0-11
# (force 4 is 11-16 kn, i.e. [11, 17)); >= 64 kn is force 12.
_BEAUFORT_UPPER_KN = [1, 4, 7, 11, 17, 22, 28, 34, 41, 48, 56, 64]


def knots_to_beaufort(knots: float) -> int:
    for force, upper in enumerate(_BEAUFORT_UPPER_KN):
        if knots < upper:
            return force
    return 12


_SECTORS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]


def degrees_to_sector(degrees: float) -> str:
    """16-point compass sector; each spans 22.5 deg centred on its heading."""
    return _SECTORS[int((degrees % 360) / 22.5 + 0.5) % 16]


def cloud_pct_to_oktas(percent: float) -> int:
    return round(percent / 100 * 8)


# Douglas sea state: inclusive upper significant-wave-height bound (m)
# per state 0-8; > 14 m is state 9 (phenomenal).
_DOUGLAS_UPPER_M = [0.0, 0.1, 0.5, 1.25, 2.5, 4.0, 6.0, 9.0, 14.0]


def wave_height_to_douglas(height_m: float) -> int:
    for state, upper in enumerate(_DOUGLAS_UPPER_M):
        if height_m <= upper:
            return state
    return 9
