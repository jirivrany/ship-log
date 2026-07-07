"""ECMWF Open Charts client: synoptic chart (MSLP + 850 hPa wind) PNG download.

Charts are licensed CC-BY-4.0 — display them with ATTRIBUTION.
"""
import os
from typing import Optional

import httpx

PRODUCT_URL = "https://charts.ecmwf.int/opencharts-api/v1/products/medium-mslp-wind850/"
DEFAULT_PROJECTION = "opencharts_south_east_europe"  # Adriatic cruising ground
ATTRIBUTION = "Chart © ECMWF (CC-BY-4.0)"
TIMEOUT_S = 30.0  # the PNG is ~1.5 MB


def fetch_synoptic_chart(
    leg_date: str,
    dest_dir: str,
    projection: str = DEFAULT_PROJECTION,
    client: Optional[httpx.Client] = None,
) -> Optional[str]:
    """Download the midday synoptic chart for a date into dest_dir.

    Returns the saved file path, or None on any failure (chart not available
    for the date, network down) — the rest of the forecast fetch must not
    depend on the chart.
    """
    params = {"valid_time": f"{leg_date}T12:00:00Z", "projection": projection}

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=TIMEOUT_S)
    try:
        try:
            response = client.get(PRODUCT_URL, params=params)
            response.raise_for_status()
            href = response.json()["data"]["link"]["href"]
            image = client.get(href)
            image.raise_for_status()
        except (httpx.HTTPError, KeyError, ValueError):
            return None
    finally:
        if own_client:
            client.close()

    os.makedirs(dest_dir, exist_ok=True)
    path = os.path.join(dest_dir, f"synoptic_{leg_date}.png")
    with open(path, "wb") as f:
        f.write(image.content)
    return path
