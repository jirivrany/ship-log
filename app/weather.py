"""Historical weather for log entries: unit derivations + Open-Meteo client."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

import httpx

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
HOURLY_VARS = "wind_speed_10m,wind_direction_10m,temperature_2m,pressure_msl,cloud_cover"
GRID_DEG = 0.25   # ERA5 cell size: points are deduplicated onto this grid
TIMEOUT_S = 10.0


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


def _grid_cell(lat: float, lon: float) -> tuple[float, float]:
    return (round(lat / GRID_DEG) * GRID_DEG, round(lon / GRID_DEG) * GRID_DEG)


def _nearest_hour_key(ts: datetime) -> str:
    rounded = (ts + timedelta(minutes=30)).replace(minute=0, second=0, microsecond=0)
    return rounded.strftime("%Y-%m-%dT%H:00")


def _hourly_value(location: dict, variable: str, hour_key: str) -> Optional[float]:
    hourly = location.get("hourly", {})
    try:
        idx = hourly["time"].index(hour_key)
    except (KeyError, ValueError):
        return None
    values = hourly.get(variable)
    return values[idx] if values else None


def fetch_weather(
    points: list[tuple[datetime, float, float]],
    client: Optional[httpx.Client] = None,
) -> list[Optional[WeatherObservation]]:
    """Historical weather for (naive-UTC timestamp, lat, lon) points.

    One batched Open-Meteo archive request for all points; each point is
    matched to its own grid cell and nearest hour.
    """
    if not points:
        return []

    cells: list[tuple[float, float]] = []
    cell_index: dict[tuple[float, float], int] = {}
    for _, lat, lon in points:
        cell = _grid_cell(lat, lon)
        if cell not in cell_index:
            cell_index[cell] = len(cells)
            cells.append(cell)

    dates = sorted(ts.date() for ts, _, _ in points)
    params = {
        "latitude": ",".join(str(lat) for lat, _ in cells),
        "longitude": ",".join(str(lon) for _, lon in cells),
        "start_date": dates[0].isoformat(),
        "end_date": dates[-1].isoformat(),
        "hourly": HOURLY_VARS,
        "wind_speed_unit": "kn",
        "cell_selection": "sea",
        "timezone": "UTC",
    }

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=TIMEOUT_S)
    try:
        response = client.get(ARCHIVE_URL, params=params)
        response.raise_for_status()
        data = response.json()

        # Sea state is best-effort: the marine grid has no cells close to
        # shore, so a failure here must not cost the atmospheric data.
        marine_params = dict(params, hourly="wave_height")
        marine_params.pop("wind_speed_unit")
        try:
            marine_response = client.get(MARINE_URL, params=marine_params)
            marine_response.raise_for_status()
            marine_data = marine_response.json()
        except httpx.HTTPError:
            marine_data = None
    finally:
        if own_client:
            client.close()

    locations = data if isinstance(data, list) else [data]
    marine_locations: list = []
    if isinstance(marine_data, list):
        marine_locations = marine_data
    elif isinstance(marine_data, dict) and "hourly" in marine_data:
        marine_locations = [marine_data]

    observations: list[Optional[WeatherObservation]] = []
    for ts, lat, lon in points:
        loc_i = cell_index[_grid_cell(lat, lon)]
        location = locations[loc_i]
        hour = _nearest_hour_key(ts)
        wind_kn = _hourly_value(location, "wind_speed_10m", hour)
        wind_deg = _hourly_value(location, "wind_direction_10m", hour)
        cloud_pct = _hourly_value(location, "cloud_cover", hour)
        wave_m = (_hourly_value(marine_locations[loc_i], "wave_height", hour)
                  if loc_i < len(marine_locations) else None)
        observations.append(WeatherObservation(
            wind_speed_kn=wind_kn,
            wind_direction=degrees_to_sector(wind_deg) if wind_deg is not None else None,
            wind_force=knots_to_beaufort(wind_kn) if wind_kn is not None else None,
            air_temperature=_hourly_value(location, "temperature_2m", hour),
            atmospheric_pressure=_hourly_value(location, "pressure_msl", hour),
            cloud_cover=cloud_pct_to_oktas(cloud_pct) if cloud_pct is not None else None,
            sea_state=wave_height_to_douglas(wave_m) if wave_m is not None else None,
        ))
    return observations
