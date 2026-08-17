"""Historical weather provider adapter (PRD §4).

Exposes normalized hourly observations, never raw provider response shapes, so
a later direct Copernicus ERA5/CERRA implementation can replace this one
without touching aggregation, classification, storage, exports, or templates.
"""
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Protocol
from zoneinfo import ZoneInfo

import httpx

from app.weather import ARCHIVE_URL

PROVIDER = "open-meteo"
MODEL = "era5"

# A whole analysis rides on these requests, so they get a longer budget than
# the log-entry weather fetch (app.weather), where a timeout merely leaves a
# field unfilled. Connecting is the part that stalls; reading is fast.
CONNECT_TIMEOUT_S = 15.0
TIMEOUT_S = 30.0
ATTEMPTS = 3
RETRY_BACKOFF_S = 0.5

# PRD §4: all five are mandatory in every analysis request.
HOURLY_VARS = (
    "wind_speed_10m,wind_direction_10m,wind_gusts_10m,precipitation,pressure_msl"
)


class ProviderError(RuntimeError):
    """The provider could not serve the requested data.

    Unlike log-entry weather enrichment (app.weather), a failure here must be
    surfaced: PRD §7 forbids a successful-looking empty analysis.
    """


@dataclass(frozen=True)
class HourlyObservation:
    """One hour at one location, in the units the analyzer works in."""
    timestamp_utc: datetime          # tz-aware UTC
    local_timestamp: datetime        # tz-aware, location's IANA zone
    wind_speed_kn: Optional[float]
    wind_direction_deg: Optional[float]
    wind_gust_kn: Optional[float]
    precipitation_mm: Optional[float]
    pressure_msl_hpa: Optional[float]

    @property
    def local_date(self) -> date:
        """The local calendar day this hour is aggregated into (PRD §8)."""
        return self.local_timestamp.date()


@dataclass(frozen=True)
class ProviderResult:
    observations: list[HourlyObservation]
    resolved_lat: float              # the cell the provider actually served
    resolved_lon: float
    provider: str
    model: str
    retrieved_at: datetime


class WeatherArchiveProvider(Protocol):
    """The seam a future Copernicus/CERRA adapter implements."""

    provider: str
    model: str

    def fetch(self, lat: float, lon: float, tz_name: str,
              start: date, end: date) -> ProviderResult:
        """Normalized hourly observations for the local dates [start, end]."""
        ...


class OpenMeteoArchiveProvider:
    """Open-Meteo Historical Weather API, pinned to the ERA5 model.

    Requests UTC rather than `timezone=<IANA>` and converts locally. The API
    applies one fixed UTC offset to a whole response, so a window crossing a
    DST transition comes back with mislabelled local hours and no repeated
    hour at all. Converting here with ZoneInfo gives true local timestamps,
    correct 23/25-hour days, and both repeated hours, as PRD §8 requires.
    """

    provider = PROVIDER
    model = MODEL

    def __init__(self, client: Optional[httpx.Client] = None,
                 attempts: int = ATTEMPTS,
                 backoff_s: float = RETRY_BACKOFF_S):
        self._client = client
        self._owned: Optional[httpx.Client] = None
        self._attempts = attempts
        self._backoff_s = backoff_s

    def _http(self) -> httpx.Client:
        """One connection pool for the whole run.

        An analysis makes a request per location and per cache gap; opening a
        fresh TLS connection for each turns every handshake into another chance
        to stall, which is how a whole run dies on one slow moment.
        """
        if self._client is not None:
            return self._client
        if self._owned is None:
            self._owned = httpx.Client(
                timeout=httpx.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
                limits=httpx.Limits(max_keepalive_connections=4),
            )
        return self._owned

    def close(self) -> None:
        if self._owned is not None:
            self._owned.close()
            self._owned = None

    def fetch(self, lat: float, lon: float, tz_name: str,
              start: date, end: date) -> ProviderResult:
        try:
            tz = ZoneInfo(tz_name)
        except Exception as exc:
            raise ProviderError(f"Unknown timezone: {tz_name}") from exc

        # Local days [start, end] span a UTC range that reaches into the
        # neighbouring days; pad by one on each side and trim after converting.
        params = {
            "latitude": lat,
            "longitude": lon,
            "start_date": (start - timedelta(days=1)).isoformat(),
            "end_date": (end + timedelta(days=1)).isoformat(),
            "hourly": HOURLY_VARS,
            "wind_speed_unit": "kn",
            "cell_selection": "sea",
            "models": self.model,
            "timezone": "UTC",
        }

        data = self._get(params, lat, lon, start, end)

        if isinstance(data, list):          # multi-location shape; we ask for one
            data = data[0]
        hourly = data.get("hourly") or {}
        times = hourly.get("time") or []
        if not times:
            raise ProviderError(
                f"Weather provider returned no hourly data for {lat},{lon} "
                f"{start}..{end}"
            )

        def series(name: str) -> list:
            return hourly.get(name) or [None] * len(times)

        observations = []
        for i, stamp in enumerate(times):
            utc_ts = datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)
            local_ts = utc_ts.astimezone(tz)
            if not (start <= local_ts.date() <= end):
                continue                    # padding outside the requested days
            observations.append(HourlyObservation(
                timestamp_utc=utc_ts,
                local_timestamp=local_ts,
                wind_speed_kn=series("wind_speed_10m")[i],
                wind_direction_deg=series("wind_direction_10m")[i],
                wind_gust_kn=series("wind_gusts_10m")[i],
                precipitation_mm=series("precipitation")[i],
                pressure_msl_hpa=series("pressure_msl")[i],
            ))

        return ProviderResult(
            observations=observations,
            resolved_lat=data.get("latitude", lat),
            resolved_lon=data.get("longitude", lon),
            provider=self.provider,
            model=self.model,
            retrieved_at=datetime.now(timezone.utc),
        )

    def _get(self, params: dict, lat: float, lon: float,
             start: date, end: date) -> dict:
        """One archive request, retried on transient transport failures.

        TLS handshakes to the archive host stall occasionally (roughly one in
        five cold connections from a container). Without a retry a single stall
        discards an entire analysis, including the years already fetched, so
        transport errors — plus 5xx and 429, where the server is having a
        moment — are retried. A 4xx is a verdict on the request itself and
        fails immediately.
        """
        client = self._http()
        last: Optional[Exception] = None

        for attempt in range(self._attempts):
            try:
                response = client.get(ARCHIVE_URL, params=params)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as exc:
                # 5xx and 429 are the server having a moment, so they are worth
                # another try; a 4xx is a verdict on the request itself.
                status = exc.response.status_code
                if status < 500 and status != 429:
                    raise ProviderError(
                        f"Weather provider rejected the request for {lat},{lon} "
                        f"{start}..{end}: {exc}"
                    ) from exc
                last = exc
                if attempt + 1 < self._attempts:
                    time.sleep(self._backoff_s * (attempt + 1))
            except httpx.TransportError as exc:
                last = exc
                if attempt + 1 < self._attempts:
                    time.sleep(self._backoff_s * (attempt + 1))
            except httpx.HTTPError as exc:
                raise ProviderError(
                    f"Weather provider request failed for {lat},{lon} "
                    f"{start}..{end}: {exc}"
                ) from exc

        raise ProviderError(
            f"Weather provider unreachable for {lat},{lon} {start}..{end} "
            f"after {self._attempts} attempts: {last}"
        ) from last
