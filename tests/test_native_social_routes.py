"""Focused tests for Instagram OAuth route safety helpers."""
from urllib.parse import urlsplit

import pytest

from cloud.config import settings
from pydantic import ValidationError

from native_social.routes import (
    InstagramPublishReconcileBody, _dashboard_redirect, _state_digest,
)


def test_oauth_state_is_stored_as_one_way_sha256_digest():
    raw = "high-entropy-oauth-state-value"
    digest = _state_digest(raw)
    assert len(digest) == 64
    assert digest != raw
    assert _state_digest(raw) == digest


def test_oauth_callback_redirect_is_fixed_and_relative():
    response = _dashboard_redirect("success")
    parsed = urlsplit(response.headers["location"])
    expected = settings.frontend_url.rstrip("/")
    assert response.headers["location"].startswith(expected + "/")
    assert parsed.path == "/"
    assert parsed.query == "instagram_connect=success"
    assert parsed.fragment == "app?tab=settings"



def test_publish_reconciliation_accepts_explicit_owner_outcomes():
    assert InstagramPublishReconcileBody(outcome="published").outcome == "published"
    assert InstagramPublishReconcileBody(outcome="not_published").outcome == "not_published"
    assert InstagramPublishReconcileBody(
        outcome="published", media_id="17890000000000000"
    ).media_id == "17890000000000000"


def test_publish_reconciliation_rejects_unsupported_outcomes():
    with pytest.raises(ValidationError):
        InstagramPublishReconcileBody(outcome="retry")
