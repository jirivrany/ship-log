"""Strava API client for the single-user, on-demand import flow.

OAuth model (Strava 2026 guidelines): client id/secret come from the
environment (.env); live tokens are persisted to a JSON file inside the
data volume because Strava rotates refresh tokens — every refresh may
return a new refresh token that must replace the stored one.

No webhooks, no background sync: every call here is user-initiated.
"""
import json
import os
import time
from typing import Optional

import httpx

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"

# Streams requested for an import; Strava omits keys the activity lacks.
STREAM_KEYS = "time,latlng,velocity_smooth,distance,temp"

# Refresh slightly before the 6-hour expiry to avoid using a token that
# dies mid-import.
EXPIRY_MARGIN_S = 60


class StravaConfigError(Exception):
    """Client id/secret not configured in the environment."""


class StravaNotAuthorized(Exception):
    """No stored tokens, or Strava rejected them — run the OAuth flow."""


def _client_creds() -> tuple[str, str]:
    client_id = os.environ.get("STRAVA_CLIENT_ID", "").strip()
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise StravaConfigError(
            "STRAVA_CLIENT_ID / STRAVA_CLIENT_SECRET are not set — "
            "create an API application at strava.com/settings/api and put "
            "the credentials in .env"
        )
    return client_id, client_secret


def _tokens_path() -> str:
    return os.environ.get("STRAVA_TOKENS_PATH", "/app/data/strava_tokens.json")


def _load_tokens() -> Optional[dict]:
    try:
        with open(_tokens_path(), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _save_tokens(data: dict) -> None:
    tokens = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_at": data["expires_at"],
    }
    path = _tokens_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(tokens, f, indent=2)


def _post(url: str, data: dict, http: Optional[httpx.Client]) -> httpx.Response:
    if http is not None:
        return http.post(url, data=data)
    with httpx.Client(timeout=20) as client:
        return client.post(url, data=data)


def _get(url: str, params: Optional[dict], headers: dict,
         http: Optional[httpx.Client]) -> httpx.Response:
    if http is not None:
        return http.get(url, params=params, headers=headers)
    with httpx.Client(timeout=30) as client:
        return client.get(url, params=params, headers=headers)


def redirect_uri() -> str:
    return os.environ.get("STRAVA_REDIRECT_URI", "http://localhost:8000/strava/callback")


def authorize_url(state: str = "/") -> str:
    client_id, _ = _client_creds()
    params = httpx.QueryParams({
        "client_id": client_id,
        "redirect_uri": redirect_uri(),
        "response_type": "code",
        "scope": "activity:read_all",
        "approval_prompt": "auto",
        "state": state,
    })
    return f"{AUTHORIZE_URL}?{params}"


def exchange_code(code: str, http: Optional[httpx.Client] = None) -> None:
    """One-time exchange of the OAuth callback code for the first token pair."""
    client_id, client_secret = _client_creds()
    r = _post(TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
    }, http)
    r.raise_for_status()
    _save_tokens(r.json())


def get_access_token(http: Optional[httpx.Client] = None) -> str:
    """Return a valid access token, refreshing (and persisting the rotated
    refresh token) when the stored one is expired or about to expire."""
    tokens = _load_tokens()
    if not tokens:
        raise StravaNotAuthorized("No stored Strava tokens")

    if tokens.get("expires_at", 0) > time.time() + EXPIRY_MARGIN_S:
        return tokens["access_token"]

    client_id, client_secret = _client_creds()
    r = _post(TOKEN_URL, {
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": tokens["refresh_token"],
        "grant_type": "refresh_token",
    }, http)
    if r.status_code in (400, 401):
        raise StravaNotAuthorized("Stored refresh token was rejected by Strava")
    r.raise_for_status()
    data = r.json()
    _save_tokens(data)
    return data["access_token"]


def _api_get(path: str, params: Optional[dict] = None,
             http: Optional[httpx.Client] = None):
    token = get_access_token(http=http)
    r = _get(f"{API_BASE}{path}", params,
             {"Authorization": f"Bearer {token}"}, http)
    if r.status_code == 401:
        raise StravaNotAuthorized("Strava rejected the access token")
    r.raise_for_status()
    return r.json()


def fetch_sail_activities(per_page: int = 50,
                          after: Optional[int] = None,
                          before: Optional[int] = None,
                          http: Optional[httpx.Client] = None) -> list[dict]:
    """One page of the athlete's timeline, filtered client-side to sailing.

    after/before are epoch seconds (Strava's own filter params) — used to
    reach past activities, e.g. a voyage window from years back."""
    params: dict = {"per_page": per_page}
    if after is not None:
        params["after"] = after
    if before is not None:
        params["before"] = before
    activities = _api_get("/athlete/activities", params, http=http)
    return [a for a in activities if a.get("sport_type") == "Sail"]


def fetch_activity_bundle(activity_id: int,
                          http: Optional[httpx.Client] = None) -> dict:
    """Everything needed to build a leg from one activity, in one dict that
    gets persisted as the leg's track file (raw API responses, reparsable)."""
    activity = _api_get(f"/activities/{activity_id}", http=http)
    streams = _api_get(
        f"/activities/{activity_id}/streams",
        {"keys": STREAM_KEYS, "key_by_type": "true"},
        http=http,
    )
    laps = _api_get(f"/activities/{activity_id}/laps", http=http)
    return {"activity": activity, "streams": streams, "laps": laps}
