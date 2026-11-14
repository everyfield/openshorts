"""Typed values shared by native social provider adapters."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

class AccountHealth(StrEnum):
    CONNECTED = "connected"
    REAUTH_REQUIRED = "reauth_required"
    REVOKED = "revoked"

class PublishStatus(StrEnum):
    PENDING = "pending"
    QUEUED = "queued"
    PUBLISHING = "publishing"
    PROCESSING = "processing"
    PUBLISHED = "published"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    CANCELLED = "cancelled"

@dataclass(frozen=True, slots=True)
class ProviderAccount:
    provider: str
    provider_account_id: str
    display_name: str
    health: AccountHealth = AccountHealth.CONNECTED
    scopes: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class PublishRequest:
    """Reference an already-rendered asset; never carry raw media bytes."""
    owner_id: str
    provider_account_id: str
    media_url: str
    caption: str
    idempotency_key: str
    options: Mapping[str, object] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class ProviderOperation:
    operation_id: str
    status: PublishStatus
    provider_post_id: str | None = None
    error_code: str | None = None

@dataclass(frozen=True, slots=True)
class PublishResult:
    operation_id: str
    status: PublishStatus
    provider_post_id: str | None = None
    error_code: str | None = None
    retryable: bool = False