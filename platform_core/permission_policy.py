"""Fail-closed policy checks for permission-gated publishing.

This module is intentionally dependency-free so it can be unit-tested and
called from FastAPI routes, queue workers, and scheduler claim handlers.
It does not send messages or publish content itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import FrozenSet


VALID_STATES = frozenset({
    "UNREQUESTED", "DRAFT", "REQUESTED", "APPROVED",
    "REJECTED", "EXPIRED", "REVOKED",
})


@dataclass(frozen=True)
class Permission:
    status: str
    allowed_platforms: FrozenSet[str]
    approved_at: datetime | None
    expires_at: datetime | None
    revoked_at: datetime | None
    commercial_use_allowed: bool = False
    editing_allowed: bool = False


@dataclass(frozen=True)
class PublishDecision:
    allowed: bool
    reason: str


def evaluate_publish_permission(
    permission: Permission | None,
    platform: str,
    *,
    now: datetime | None = None,
    commercial_use: bool = False,
    editing_required: bool = False,
) -> PublishDecision:
    """Return an explicit decision; missing, malformed or stale evidence blocks."""
    if permission is None:
        return PublishDecision(False, "permission_record_missing")
    if permission.status not in VALID_STATES:
        return PublishDecision(False, "permission_state_invalid")
    if permission.status != "APPROVED":
        return PublishDecision(False, f"permission_not_approved:{permission.status.lower()}")
    if permission.approved_at is None:
        return PublishDecision(False, "approval_timestamp_missing")

    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        return PublishDecision(False, "clock_must_be_timezone_aware")
    if permission.revoked_at is not None:
        return PublishDecision(False, "permission_revoked")
    if permission.expires_at is not None:
        if permission.expires_at.tzinfo is None:
            return PublishDecision(False, "expiry_must_be_timezone_aware")
        if current >= permission.expires_at:
            return PublishDecision(False, "permission_expired")
    if platform not in permission.allowed_platforms:
        return PublishDecision(False, "platform_not_authorized")
    if commercial_use and not permission.commercial_use_allowed:
        return PublishDecision(False, "commercial_use_not_authorized")
    if editing_required and not permission.editing_allowed:
        return PublishDecision(False, "editing_not_authorized")
    return PublishDecision(True, "permission_valid")
