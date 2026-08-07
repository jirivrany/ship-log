"""Token lifecycle for the Strava client: storage, refresh rotation, failure
modes. HTTP is served by httpx.MockTransport; no live API calls."""
import json
import time

import httpx
import pytest

from app import strava_api


@pytest.fixture(autouse=True)
def strava_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STRAVA_TOKENS_PATH", str(tmp_path / "tokens.json"))
    monkeypatch.setenv("STRAVA_CLIENT_ID", "123")
    monkeypatch.setenv("STRAVA_CLIENT_SECRET", "secret")
    return tmp_path


def _write_tokens(tmp_path, access="old-access", refresh="old-refresh", expires_in=10_000):
    (tmp_path / "tokens.json").write_text(json.dumps({
        "access_token": access,
        "refresh_token": refresh,
        "expires_at": int(time.time()) + expires_in,
    }))


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# --- config / bootstrap ---

def test_authorize_url_contains_scope_and_redirect():
    url = strava_api.authorize_url(state="/voyages/1/strava")
    assert "activity%3Aread_all" in url or "activity:read_all" in url
    assert "localhost%3A8000%2Fstrava%2Fcallback" in url or "localhost:8000/strava/callback" in url
    assert "state=%2Fvoyages%2F1%2Fstrava" in url


def test_missing_creds_raise_config_error(monkeypatch):
    monkeypatch.delenv("STRAVA_CLIENT_ID")
    with pytest.raises(strava_api.StravaConfigError):
        strava_api.authorize_url()


def test_exchange_code_persists_tokens(strava_env):
    def handler(request):
        assert request.url.path == "/oauth/token"
        assert b"grant_type=authorization_code" in request.read()
        return httpx.Response(200, json={
            "access_token": "first-access",
            "refresh_token": "first-refresh",
            "expires_at": int(time.time()) + 21600,
        })

    strava_api.exchange_code("the-code", http=_client(handler))
    saved = json.loads((strava_env / "tokens.json").read_text())
    assert saved["access_token"] == "first-access"
    assert saved["refresh_token"] == "first-refresh"


# --- get_access_token ---

def test_no_tokens_raises_not_authorized():
    with pytest.raises(strava_api.StravaNotAuthorized):
        strava_api.get_access_token()


def test_fresh_token_used_without_http(strava_env):
    _write_tokens(strava_env)

    def handler(request):  # any request means the fresh token wasn't trusted
        raise AssertionError("no HTTP call expected for a fresh token")

    assert strava_api.get_access_token(http=_client(handler)) == "old-access"


def test_expired_token_refreshes_and_rotates(strava_env):
    """Strava rotates refresh tokens — the new one must replace the stored one."""
    _write_tokens(strava_env, expires_in=-10)

    def handler(request):
        body = request.read().decode()
        assert "grant_type=refresh_token" in body
        assert "old-refresh" in body
        return httpx.Response(200, json={
            "access_token": "new-access",
            "refresh_token": "rotated-refresh",
            "expires_at": int(time.time()) + 21600,
        })

    assert strava_api.get_access_token(http=_client(handler)) == "new-access"
    saved = json.loads((strava_env / "tokens.json").read_text())
    assert saved["refresh_token"] == "rotated-refresh"


def test_rejected_refresh_raises_not_authorized(strava_env):
    _write_tokens(strava_env, expires_in=-10)

    def handler(request):
        return httpx.Response(400, json={"message": "Bad Request"})

    with pytest.raises(strava_api.StravaNotAuthorized):
        strava_api.get_access_token(http=_client(handler))


# --- API calls ---

def test_fetch_sail_activities_filters_and_authenticates(strava_env):
    _write_tokens(strava_env)

    def handler(request):
        assert request.url.path == "/api/v3/athlete/activities"
        assert request.headers["Authorization"] == "Bearer old-access"
        return httpx.Response(200, json=[
            {"id": 1, "sport_type": "Sail", "name": "Zadar - Muline"},
            {"id": 2, "sport_type": "Ride", "name": "Bike ride"},
            {"id": 3, "sport_type": "Sail", "name": "Muline - Zadar"},
        ])

    result = strava_api.fetch_sail_activities(http=_client(handler))
    assert [a["id"] for a in result.activities] == [1, 3]
    assert result.scanned == 3


def test_fetch_activities_passes_window_params(strava_env):
    _write_tokens(strava_env)

    def handler(request):
        assert request.url.params["after"] == "1747000000"
        assert request.url.params["before"] == "1748000000"
        return httpx.Response(200, json=[])

    strava_api.fetch_sail_activities(after=1747000000, before=1748000000,
                                     http=_client(handler))


def test_fetch_activities_omits_unset_window(strava_env):
    _write_tokens(strava_env)

    def handler(request):
        assert "after" not in request.url.params
        assert "before" not in request.url.params
        return httpx.Response(200, json=[])

    strava_api.fetch_sail_activities(http=_client(handler))


# --- pagination ---

def _paging_handler(pages, seen=None):
    """Serve `pages` (list of activity lists) by requested page number."""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        if seen is not None:
            seen.append(page)
        body = pages[page - 1] if page <= len(pages) else []
        return httpx.Response(200, json=body)
    return handler


def _acts(start_id, count, sport="Ride"):
    return [{"id": start_id + i, "sport_type": sport, "name": f"a{start_id + i}"}
            for i in range(count)]


def test_sail_activities_found_beyond_first_page(strava_env):
    """The original bug: a full page of non-sailing activities hid the sail
    ones that sat on later pages."""
    _write_tokens(strava_env)
    pages = [
        _acts(1, 50, "Ride"),
        _acts(51, 50, "Run"),
        _acts(101, 3, "Sail"),
    ]
    result = strava_api.fetch_sail_activities(per_page=50,
                                              http=_client(_paging_handler(pages)))
    assert [a["id"] for a in result.activities] == [101, 102, 103]
    assert result.scanned == 103
    assert result.truncated is False


def test_pagination_stops_on_short_page(strava_env):
    """A page smaller than per_page is the last one — don't request another."""
    _write_tokens(strava_env)
    seen = []
    pages = [_acts(1, 50, "Sail"), _acts(51, 10, "Sail")]
    result = strava_api.fetch_sail_activities(
        per_page=50, http=_client(_paging_handler(pages, seen)))
    assert seen == [1, 2]
    assert len(result.activities) == 60
    assert result.truncated is False


def test_pagination_stops_on_empty_page(strava_env):
    """An exact multiple of per_page needs one more request to learn it ended."""
    _write_tokens(strava_env)
    seen = []
    result = strava_api.fetch_sail_activities(
        per_page=50, http=_client(_paging_handler([_acts(1, 50, "Sail")], seen)))
    assert seen == [1, 2]
    assert len(result.activities) == 50
    assert result.truncated is False


def test_pagination_caps_at_max_pages(strava_env):
    """A runaway window must not hammer the API past the cap."""
    _write_tokens(strava_env)
    seen = []
    pages = [_acts(1 + i * 50, 50, "Ride") for i in range(strava_api.MAX_PAGES + 3)]
    result = strava_api.fetch_sail_activities(
        per_page=50, http=_client(_paging_handler(pages, seen)))
    assert seen == list(range(1, strava_api.MAX_PAGES + 1))
    assert result.truncated is True
    assert result.scanned == strava_api.MAX_PAGES * 50


def test_window_params_repeat_on_every_page(strava_env):
    """after/before must survive pagination or later pages drift out of window."""
    _write_tokens(strava_env)
    windows = []

    def handler(request):
        windows.append((request.url.params.get("after"),
                        request.url.params.get("before")))
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=_acts(1 + (page - 1) * 50, 50, "Sail")
                              if page == 1 else [])

    strava_api.fetch_sail_activities(after=1747000000, before=1748000000,
                                     per_page=50, http=_client(handler))
    assert len(windows) == 2
    assert all(w == ("1747000000", "1748000000") for w in windows)


def test_api_401_raises_not_authorized(strava_env):
    _write_tokens(strava_env)

    def handler(request):
        return httpx.Response(401, json={"message": "Unauthorized"})

    with pytest.raises(strava_api.StravaNotAuthorized):
        strava_api.fetch_sail_activities(http=_client(handler))
