"""Provider adapter interface. Implementations must keep credentials server-side."""
from __future__ import annotations
from typing import Protocol, runtime_checkable
from .types import ProviderAccount, ProviderOperation, PublishRequest, PublishResult

@runtime_checkable
class SocialProvider(Protocol):
    """Contract shared by YouTube, Instagram and TikTok adapters.

    Implementations must not log OAuth tokens, authorization codes, signed
    media URLs, or secret-bearing request bodies. Publish timeouts after
    transmission are ambiguous and must not trigger blind duplicate writes.
    """
    @property
    def provider_name(self) -> str:
        """Stable lowercase provider key."""

    async def validate_account(self, account_id: str) -> ProviderAccount:
        """Return account identity/health without exposing credentials."""

    async def publish(self, request: PublishRequest) -> PublishResult:
        """Start one provider publication operation."""

    async def get_publish_status(self, operation_id: str) -> ProviderOperation:
        """Read provider status; never initiate another upload."""

    async def revoke_account(self, account_id: str) -> None:
        """Revoke/disconnect provider authorization where supported."""