"""Open-Meteo archive adapter for weather analysis (PRD §4, §8).

HTTP is served by httpx.MockTransport from fixtures recorded off the real API
(scripts/record_weather_analysis_fixtures.py); no live calls.
"""
import json
import os
from collections import Counter
from datetime import date, datetime, timezone

import httpx
import pytest

from app.analysis.provider import (
    HOURLY_VARS,
    OpenMeteoArchiveProvider,
    ProviderError,
)

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
ZAGREB = "Europe/Zagreb"
MURTER = (43.82, 15.60)


def _fixture(name: str):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def _provider(payload, requests: list, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if status != 200:
            return httpx.Response(status)
        return httpx.Response(200, json=payload)
    return OpenMeteoArchiveProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_request_carries_every_mandatory_parameter():
    """PRD §4/AC4: era5, sea cell, knots, and all five hourly variables."""
    requests = []
    provider = _provider(_fixture("analysis_archive_murter.json"), requests)

    provider.fetch(*MURTER, ZAGREB, date(2024, 10, 24), date(2024, 10, 30))

    assert len(requests) == 1
    params = requests[0].url.params
    assert params["models"] == "era5"
    assert params["cell_selection"] == "sea"
    assert params["wind_speed_unit"] == "kn"
    requested = set(params["hourly"].split(","))
    assert requested == {
        "wind_speed_10m", "wind_direction_10m", "wind_gusts_10m",
        "precipitation", "pressure_msl",
    }
    assert set(HOURLY_VARS.split(",")) == requested


def test_requests_utc_and_pads_the_range_by_a_day():
    """Local days need UTC hours from the neighbouring days, so the request
    is padded and the surplus trimmed after conversion."""
    requests = []
    provider = _provider(_fixture("analysis_archive_murter.json"), requests)

    provider.fetch(*MURTER, ZAGREB, date(2024, 10, 24), date(2024, 10, 30))

    params = requests[0].url.params
    assert params["timezone"] == "UTC"
    assert params["start_date"] == "2024-10-23"
    assert params["end_date"] == "2024-10-31"


def test_observations_are_trimmed_to_the_requested_local_days():
    provider = _provider(_fixture("analysis_archive_murter.json"), [])

    result = provider.fetch(*MURTER, ZAGREB,
                            date(2024, 10, 24), date(2024, 10, 30))

    local_days = {o.local_date for o in result.observations}
    assert min(local_days) == date(2024, 10, 24)
    assert max(local_days) == date(2024, 10, 30)


def test_normalizes_values_and_both_timestamps():
    provider = _provider(_fixture("analysis_archive_murter.json"), [])

    result = provider.fetch(*MURTER, ZAGREB,
                            date(2024, 10, 24), date(2024, 10, 24))

    first = result.observations[0]
    # 24 Oct is still CEST (+2), so the local day starts at 22:00Z the day before
    assert first.local_timestamp.isoformat().startswith("2024-10-24T00:00")
    assert first.timestamp_utc == datetime(2024, 10, 23, 22, 0, tzinfo=timezone.utc)
    assert first.local_date == date(2024, 10, 24)
    # every normalized field is populated from the recorded response
    assert first.wind_speed_kn is not None
    assert first.wind_direction_deg is not None
    assert first.wind_gust_kn is not None
    assert first.precipitation_mm is not None
    assert first.pressure_msl_hpa is not None


def test_dst_end_day_has_25_hours_with_the_repeated_hour_kept():
    """PRD §8: convert UTC to local ourselves so the repeated local hour
    survives. Open-Meteo's own timezone= applies one fixed offset to the whole
    response and would silently drop it."""
    provider = _provider(_fixture("analysis_archive_dst.json"), [])

    result = provider.fetch(*MURTER, ZAGREB,
                            date(2024, 10, 26), date(2024, 10, 28))

    per_day = Counter(o.local_date for o in result.observations)
    assert per_day[date(2024, 10, 26)] == 24
    assert per_day[date(2024, 10, 27)] == 25   # summer time ends: 25-hour day
    assert per_day[date(2024, 10, 28)] == 24

    # 02:00 local occurs twice, once in CEST and once in CET
    repeated = [o for o in result.observations
                if o.local_timestamp.strftime("%Y-%m-%d %H:%M") == "2024-10-27 02:00"]
    assert len(repeated) == 2
    assert {o.local_timestamp.fold for o in repeated} == {0, 1}
    # they are distinct instants an hour apart, and both are retained
    assert (repeated[1].timestamp_utc - repeated[0].timestamp_utc).total_seconds() == 3600


def test_reports_the_resolved_sea_cell():
    """The sea-cell walk moves the point; callers need the cell actually served."""
    provider = _provider(_fixture("analysis_archive_murter.json"), [])

    result = provider.fetch(*MURTER, ZAGREB,
                            date(2024, 10, 24), date(2024, 10, 30))

    assert (result.resolved_lat, result.resolved_lon) == (43.75, 15.5)
    assert (result.resolved_lat, result.resolved_lon) != MURTER
    assert result.provider == "open-meteo"
    assert result.model == "era5"


def test_http_failure_raises_instead_of_returning_empty():
    """PRD §7: a failure must not become a successful-looking empty analysis."""
    requests = []
    provider = _provider(None, requests, status=500)

    with pytest.raises(ProviderError):
        provider.fetch(*MURTER, ZAGREB, date(2024, 10, 24), date(2024, 10, 30))


def test_empty_hourly_payload_raises():
    provider = _provider({"latitude": 43.75, "longitude": 15.5, "hourly": {}}, [])

    with pytest.raises(ProviderError, match="no hourly data"):
        provider.fetch(*MURTER, ZAGREB, date(2024, 10, 24), date(2024, 10, 30))


def test_unknown_timezone_raises_without_calling_the_provider():
    requests = []
    provider = _provider(_fixture("analysis_archive_murter.json"), requests)

    with pytest.raises(ProviderError, match="Unknown timezone"):
        provider.fetch(*MURTER, "Mars/Olympus",
                       date(2024, 10, 24), date(2024, 10, 30))
    assert requests == []


# --- transient transport failures -------------------------------------------

def _flaky_provider(failures: int, payload, attempts=3):
    """Fails the first `failures` requests with a TLS-style connect stall."""
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) <= failures:
            raise httpx.ConnectTimeout(
                "_ssl.c:993: The handshake operation timed out")
        return httpx.Response(200, json=payload)

    provider = OpenMeteoArchiveProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        attempts=attempts,
        backoff_s=0,          # no real sleeping in tests
    )
    return provider, calls


def test_transient_handshake_stall_is_retried():
    """A TLS stall must not discard a whole analysis: observed roughly one in
    five cold connections from a container."""
    payload = _fixture("analysis_archive_murter.json")
    provider, calls = _flaky_provider(failures=1, payload=payload)

    result = provider.fetch(*MURTER, ZAGREB,
                            date(2024, 10, 24), date(2024, 10, 30))

    assert len(calls) == 2          # one failure, then success
    assert result.observations


def test_retries_are_bounded():
    payload = _fixture("analysis_archive_murter.json")
    provider, calls = _flaky_provider(failures=99, payload=payload, attempts=3)

    with pytest.raises(ProviderError, match="unreachable.*after 3 attempts"):
        provider.fetch(*MURTER, ZAGREB, date(2024, 10, 24), date(2024, 10, 30))

    assert len(calls) == 3


def _status_provider(status: int, attempts: int = 3):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(status)

    return OpenMeteoArchiveProvider(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        attempts=attempts,
        backoff_s=0,          # no real sleeping in tests
    ), calls


@pytest.mark.parametrize("status", [400, 404])
def test_client_errors_are_not_retried(status):
    """A 4xx is a verdict on the request — retrying it just wastes time."""
    provider, calls = _status_provider(status)

    with pytest.raises(ProviderError, match="rejected the request"):
        provider.fetch(*MURTER, ZAGREB, date(2024, 10, 24), date(2024, 10, 30))

    assert len(calls) == 1


@pytest.mark.parametrize("status", [500, 503, 429])
def test_server_errors_and_rate_limits_are_retried(status):
    """The server having a moment is worth another try."""
    provider, calls = _status_provider(status)

    with pytest.raises(ProviderError, match="after 3 attempts"):
        provider.fetch(*MURTER, ZAGREB, date(2024, 10, 24), date(2024, 10, 30))

    assert len(calls) == 3


def test_owned_client_is_reused_across_fetches():
    """One connection pool per run: a fresh TLS handshake per request is what
    made a stall likely in the first place."""
    provider = OpenMeteoArchiveProvider()
    try:
        first = provider._http()
        assert provider._http() is first
    finally:
        provider.close()


def test_close_is_safe_to_call_twice():
    provider = OpenMeteoArchiveProvider()
    provider._http()
    provider.close()
    provider.close()          # must not raise
