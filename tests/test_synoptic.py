"""ECMWF Open Charts client: product metadata -> PNG download -> stored file.

Product JSON is a recorded real response (scripts/record_forecast_fixtures.py);
the PNG body is a stand-in — the client treats it as opaque bytes.
"""
import json
import os

import httpx

from app.synoptic import fetch_synoptic_chart

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-chart-bytes"


def _product_fixture():
    with open(os.path.join(FIXTURES, "ecmwf_product.json"), encoding="utf-8") as f:
        return json.load(f)


def _client(requests: list, product_status=200, image_status=200) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if "opencharts-api" in request.url.path:
            return httpx.Response(product_status, json=_product_fixture())
        return httpx.Response(image_status, content=PNG_BYTES,
                              headers={"content-type": "image/png"})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_downloads_chart_to_dest_dir(tmp_path):
    requests = []
    client = _client(requests)

    path = fetch_synoptic_chart("2026-07-08", str(tmp_path / "leg"), client=client)

    assert path == str(tmp_path / "leg" / "synoptic_2026-07-08.png")
    with open(path, "rb") as f:
        assert f.read() == PNG_BYTES
    # first request: product metadata with date + projection; second: the PNG href
    params = requests[0].url.params
    assert params["valid_time"] == "2026-07-08T12:00:00Z"
    assert params["projection"] == "opencharts_south_east_europe"
    assert requests[1].url.host == "charts.ecmwf.int"
    assert "/content/" in requests[1].url.path


def test_product_error_returns_none(tmp_path):
    # e.g. date outside the forecast horizon — the API answers an error
    client = _client([], product_status=404)
    assert fetch_synoptic_chart("2020-01-01", str(tmp_path), client=client) is None
    assert not os.listdir(tmp_path)


def test_image_download_error_returns_none(tmp_path):
    client = _client([], image_status=500)
    assert fetch_synoptic_chart("2026-07-08", str(tmp_path), client=client) is None


def test_network_timeout_returns_none(tmp_path):
    def handler(request):
        raise httpx.ConnectTimeout("boom")
    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert fetch_synoptic_chart("2026-07-08", str(tmp_path), client=client) is None
