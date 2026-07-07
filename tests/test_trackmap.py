"""Track-map renderer: OSM tiles are mocked; the polyline drawing is real."""
import io

import httpx
from PIL import Image

from app.trackmap import TRACK_COLOR, render_track_map

# A short hop in the Zadar channel
TRACK = [(44.05, 15.30), (44.03, 15.28), (44.01, 15.27), (43.99, 15.25)]


def _tile_png(color=(230, 230, 230)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (256, 256), color).save(buf, format="PNG")
    return buf.getvalue()


def _tile_client(requests: list) -> httpx.Client:
    tile = _tile_png()
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=tile, headers={"content-type": "image/png"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_renders_track_on_tiles():
    requests = []
    png = render_track_map(TRACK, client=_tile_client(requests))

    image = Image.open(io.BytesIO(png))
    assert image.size == (1000, 640)
    # the polyline was drawn over the plain tiles
    assert TRACK_COLOR in {color for _, color in image.getcolors(2**20)}
    # OSM tile usage policy: identify ourselves
    assert "ship-log" in requests[0].headers["user-agent"]


def test_custom_size():
    png = render_track_map(TRACK, width=400, height=300, client=_tile_client([]))
    assert Image.open(io.BytesIO(png)).size == (400, 300)


def test_single_point_yields_none():
    assert render_track_map([(44.05, 15.30)], client=_tile_client([])) is None


def test_tile_failure_yields_none():
    def handler(request):
        return httpx.Response(503)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert render_track_map(TRACK, client=client) is None


def test_network_timeout_yields_none():
    def handler(request):
        raise httpx.ConnectTimeout("boom")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert render_track_map(TRACK, client=client) is None
