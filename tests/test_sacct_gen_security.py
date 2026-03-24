import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hpcperfstats_tools.sacct_gen import send_to_api


def _response(status_code=200, location=None, json_data=None):
    resp = Mock()
    resp.status_code = status_code
    resp.is_redirect = status_code in (301, 302, 303, 307, 308)
    resp.headers = {}
    if location is not None:
        resp.headers["Location"] = location
    resp.raise_for_status.return_value = None
    resp.json.return_value = json_data or {"inserted": 1}
    return resp


def test_send_to_api_follows_same_origin_redirect():
    first = _response(status_code=302, location="https://example.org/ingest-alt")
    second = _response(status_code=200, json_data={"inserted": 9})

    with patch("hpcperfstats_tools.sacct_gen.requests.post", side_effect=[first, second]) as post:
        ok, inserted = send_to_api("https://example.org", "secret-key", "2026-03-23", "payload")

    assert ok is True
    assert inserted == 9
    assert post.call_count == 2
    second_headers = post.call_args_list[1].kwargs["headers"]
    assert second_headers["X-API-Key"] == "secret-key"


def test_send_to_api_blocks_cross_origin_redirect():
    first = _response(status_code=302, location="https://evil.example.net/steal")

    with patch("hpcperfstats_tools.sacct_gen.requests.post", side_effect=[first]) as post:
        ok, message = send_to_api("https://example.org", "secret-key", "2026-03-23", "payload")

    assert ok is False
    assert "Cross-origin redirect blocked" in message
    assert post.call_count == 1


def test_send_to_api_follows_relative_redirect_on_same_origin():
    first = _response(status_code=307, location="/api/sacct/ingest/?date=2026-03-23")
    second = _response(status_code=200, json_data={"inserted": 2})

    with patch("hpcperfstats_tools.sacct_gen.requests.post", side_effect=[first, second]) as post:
        ok, inserted = send_to_api("https://example.org/api", "secret-key", "2026-03-23", "payload")

    assert ok is True
    assert inserted == 2
    second_url = post.call_args_list[1].args[0]
    assert second_url == "https://example.org/api/sacct/ingest/?date=2026-03-23"
