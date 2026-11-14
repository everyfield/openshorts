"""Focused tests for Instagram OAuth route safety helpers."""
from urllib.parse import urlsplit

from native_social.routes import _dashboard_redirect, _state_digest


def test_oauth_state_is_stored_as_one_way_sha256_digest():
    raw = "high-entropy-oauth-state-value"
    digest = _state_digest(raw)
    assert len(digest) == 64
    assert digest != raw
    assert _state_digest(raw) == digest


def test_oauth_callback_redirect_is_fixed_and_relative():
    response = _dashboard_redirect("success")
    parsed = urlsplit(response.headers["location"])
    assert parsed.scheme == ""
    assert parsed.netloc == ""
    assert parsed.path == "/"
    assert parsed.query == "instagram_connect=success"
