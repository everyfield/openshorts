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


def _aware(value: datetime | None) -> bool:
    return value is not None and value.tzinfo is not None and value.utcoffset() is not None


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
    if not _aware(permission.approved_at):
        return PublishDecision(False, "approval_timestamp_missing_or_invalid")

    current = now or datetime.now(timezone.utc)
    if not _aware(current):
        return PublishDecision(False, "clock_must_be_timezone_aware")
    if permission.approved_at > current:
        return PublishDecision(False, "approval_timestamp_in_future")
    if permission.revoked_at is not None:
        if not _aware(permission.revoked_at):
            return PublishDecision(False, "revocation_timestamp_invalid")
        return PublishDecision(False, "permission_revoked")
    if permission.expires_at is not None:
        if not _aware(permission.expires_at):
            return PublishDecision(False, "expiry_must_be_timezone_aware")
        if current >= permission.expires_at:
            return PublishDecision(False, "permission_expired")
    if not platform or platform not in permission.allowed_platforms:
        return PublishDecision(False, "platform_not_authorized")
    if commercial_use and not permission.commercial_use_allowed:
        return PublishDecision(False, "commercial_use_not_authorized")
    if editing_required and not permission.editing_allowed:
        return PublishDecision(False, "editing_not_authorized")
    return PublishDecision(True, "permission_valid")
