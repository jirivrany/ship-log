"""OpenStreetMap coastline geometry for a bounding box, via the Overpass API.

The seamark tiles the map draws are rendered PNGs, so the shoreline cannot be
recovered from them; the geometry has to come from the OSM data itself.

Overpass is a volunteer service under a fair-use policy: roughly 100 queries
and 10 MB a day for an application, no parallel scripts, and a User-Agent that
identifies the caller. Every answer is therefore cached on disk and re-fetched
only when a bbox has never been seen. The main instance is frequently
overloaded and answers 504 under load, so requests retry with a backoff.
"""
import hashlib
import json
import os
import time
from dataclasses import dataclass
from typing import Optional, Sequence

import httpx

ENDPOINT = "https://overpass-api.de/api/interpreter"

# The fair-use policy requires a User-Agent that names the application.
USER_AGENT = "ShipLog/1.0 (personal sailing logbook)"

ATTRIBUTION = "Coastline © OpenStreetMap contributors (ODbL)"

# The query can take a while server-side on a large box; the client waits a
# little longer than Overpass's own timeout so the server's error is seen
# rather than a client-side abort.
QUERY_TIMEOUT_S = 180
CLIENT_TIMEOUT_S = 200.0

# Transient 504s are normal on the public instance. Three attempts with a
# growing pause is enough in practice without hammering a struggling server.
MAX_ATTEMPTS = 3
BACKOFF_S = (5.0, 20.0)


class CoastlineError(RuntimeError):
    """Coastline data could not be fetched."""


@dataclass(frozen=True)
class BoundingBox:
    south: float
    west: float
    north: float
    east: float

    def __post_init__(self):
        if not (-90 <= self.south < self.north <= 90):
            raise ValueError("latitudes must satisfy -90 <= south < north <= 90")
        if not (-180 <= self.west < self.east <= 180):
            raise ValueError("longitudes must satisfy -180 <= west < east <= 180")

    def padded(self, margin_deg: float) -> "BoundingBox":
        """Grown by a margin, clamped to valid coordinates.

        The contour needs coastline from beyond the visible area: land just
        outside the view still pulls the limit line inside it.
        """
        return BoundingBox(
            south=max(-90.0, self.south - margin_deg),
            west=max(-180.0, self.west - margin_deg),
            north=min(90.0, self.north + margin_deg),
            east=min(180.0, self.east + margin_deg),
        )

    @property
    def cache_key(self) -> str:
        # Rounded so that a pan of a few metres reuses the cached answer
        # instead of asking Overpass again.
        rounded = tuple(round(v, 2) for v in
                        (self.south, self.west, self.north, self.east))
        digest = hashlib.sha256(str(rounded).encode()).hexdigest()[:16]
        return f"coastline-{digest}"


def _query(bbox: BoundingBox) -> str:
    return (
        f"[out:json][timeout:{QUERY_TIMEOUT_S}];"
        f"way[\"natural\"=\"coastline\"]"
        f"({bbox.south},{bbox.west},{bbox.north},{bbox.east});"
        f"out geom;"
    )


def _ways_from_response(payload: dict) -> list[list[tuple[float, float]]]:
    """Coastline ways as lists of (lat, lon), dropping anything degenerate."""
    ways = []
    for element in payload.get("elements", ()):
        geometry = element.get("geometry") or ()
        if len(geometry) < 2:
            continue
        ways.append([(point["lat"], point["lon"]) for point in geometry])
    return ways


def fetch_coastline(
    bbox: BoundingBox,
    cache_dir: str,
    client: Optional[httpx.Client] = None,
) -> list[list[tuple[float, float]]]:
    """Coastline ways inside bbox, from the on-disk cache or from Overpass.

    Raises CoastlineError when the data cannot be fetched; the caller turns
    that into a message rather than an empty map, because "no coastline here"
    and "the server is busy" mean very different things to a skipper.
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache_path = os.path.join(cache_dir, f"{bbox.cache_key}.json")

    if os.path.exists(cache_path):
        try:
            with open(cache_path) as handle:
                return [[(p[0], p[1]) for p in way] for way in json.load(handle)]
        except (OSError, ValueError, TypeError, IndexError):
            # A truncated or hand-edited cache file must not be fatal.
            pass

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=CLIENT_TIMEOUT_S,
                              headers={"User-Agent": USER_AGENT})
    try:
        last_error = "unknown error"
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = client.post(ENDPOINT, content=_query(bbox).encode())
                if response.status_code == 200:
                    ways = _ways_from_response(response.json())
                    break
                # 429 and 504 are the documented "busy, come back later"
                # answers; anything else is unlikely to improve on a retry.
                if response.status_code not in (429, 504):
                    raise CoastlineError(
                        f"Overpass returned HTTP {response.status_code}")
                last_error = f"Overpass is busy (HTTP {response.status_code})"
            except (httpx.HTTPError, ValueError) as exc:
                last_error = f"Overpass request failed ({type(exc).__name__})"

            if attempt < len(BACKOFF_S):
                time.sleep(BACKOFF_S[attempt])
        else:
            raise CoastlineError(
                f"{last_error} — the public Overpass instance is often "
                f"overloaded. Please try again in a minute.")
    finally:
        if own_client:
            client.close()

    try:
        with open(cache_path, "w") as handle:
            json.dump(ways, handle)
    except OSError:
        # An unwritable cache is a performance problem, not a failure.
        pass

    return ways
