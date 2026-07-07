"""Static track-map rendering for the PDF export: OSM tiles + track polyline.

Produces a PNG image of the leg's track on OpenStreetMap raster tiles
(matching the app's Leaflet look). Tile fetching is best-effort: any
failure yields None and the export simply carries no map.
"""
import io
import math
from typing import Optional

import httpx
from PIL import Image, ImageDraw

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_SIZE = 256
MAX_ZOOM = 16
# OSM tile usage policy requires an identifying User-Agent
HEADERS = {"User-Agent": "ship-log/1.0 (personal sailing logbook; PDF export)"}
ATTRIBUTION = "© OpenStreetMap contributors"
TIMEOUT_S = 30.0

TRACK_COLOR = (37, 99, 235)      # same blue as the Leaflet polyline (#2563eb)
START_COLOR = (22, 163, 74)      # green
END_COLOR = (220, 38, 38)        # red


def _global_px(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Web-Mercator global pixel coordinates at a zoom level."""
    scale = TILE_SIZE * (2 ** zoom)
    x = (lon + 180.0) / 360.0 * scale
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * scale
    return x, y


def _fit_zoom(points: list[tuple[float, float]], width: int, height: int) -> int:
    """Highest zoom at which the track's bbox fits in ~90% of the viewport."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    for zoom in range(MAX_ZOOM, 0, -1):
        x0, y0 = _global_px(max(lats), min(lons), zoom)
        x1, y1 = _global_px(min(lats), max(lons), zoom)
        if abs(x1 - x0) <= width * 0.9 and abs(y1 - y0) <= height * 0.9:
            return zoom
    return 1


def render_track_map(
    points: list[tuple[float, float]],
    width: int = 1000,
    height: int = 640,
    client: Optional[httpx.Client] = None,
) -> Optional[bytes]:
    """PNG bytes of the track drawn on OSM tiles; None if there is nothing
    to draw or the tiles cannot be fetched."""
    if len(points) < 2:
        return None

    zoom = _fit_zoom(points, width, height)
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    cx, cy = _global_px((min(lats) + max(lats)) / 2, (min(lons) + max(lons)) / 2, zoom)
    left, top = cx - width / 2, cy - height / 2

    image = Image.new("RGB", (width, height))
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=TIMEOUT_S, headers=HEADERS)
    try:
        n_tiles = 2 ** zoom
        for tx in range(int(left // TILE_SIZE), int((left + width) // TILE_SIZE) + 1):
            for ty in range(int(top // TILE_SIZE), int((top + height) // TILE_SIZE) + 1):
                if not (0 <= ty < n_tiles):
                    continue
                try:
                    response = client.get(
                        TILE_URL.format(z=zoom, x=tx % n_tiles, y=ty), headers=HEADERS
                    )
                    response.raise_for_status()
                    tile = Image.open(io.BytesIO(response.content)).convert("RGB")
                except (httpx.HTTPError, OSError):
                    return None
                image.paste(tile, (int(tx * TILE_SIZE - left), int(ty * TILE_SIZE - top)))
    finally:
        if own_client:
            client.close()

    draw = ImageDraw.Draw(image)
    px = []
    for lat, lon in points:
        gx, gy = _global_px(lat, lon, zoom)
        px.append((gx - left, gy - top))
    draw.line(px, fill=TRACK_COLOR, width=4, joint="curve")
    for (x, y), color in ((px[0], START_COLOR), (px[-1], END_COLOR)):
        draw.ellipse((x - 6, y - 6, x + 6, y + 6), fill=color, outline=(255, 255, 255), width=2)

    # attribution box bottom-right (OSM licence requirement)
    text_w = draw.textlength(ATTRIBUTION)
    draw.rectangle((width - text_w - 10, height - 18, width, height), fill=(255, 255, 255))
    draw.text((width - text_w - 5, height - 15), ATTRIBUTION, fill=(60, 60, 60))

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()
