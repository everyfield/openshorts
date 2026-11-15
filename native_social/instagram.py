"""Instagram Login API client for professional-account Reel publishing.

Uses Meta's official Instagram API with Instagram Login. OAuth credentials
and access tokens must be held server-side and encrypted at rest by the caller.
"""
from __future__ import annotations

import ipaddress
import os
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

from .types import AccountHealth, ProviderAccount, ProviderOperation, PublishStatus


AUTH_BASE = "https://www.instagram.com/oauth/authorize"
TOKEN_ENDPOINT = "https://api.instagram.com/oauth/access_token"
GRAPH_BASE = "https://graph.instagram.com"
DEFAULT_SCOPES = (
    "instagram_business_basic",
    "instagram_business_content_publish",
)


class InstagramAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, error_code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code


class InstagramAPI:
    """Official Instagram Login + Graph API transport (Reels only)."""

    def __init__(
        self, *, app_id: str | None = None, app_secret: str | None = None,
        redirect_uri: str | None = None, api_version: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ):
        self.app_id = (app_id or os.environ.get("INSTAGRAM_APP_ID", "")).strip()
        self.app_secret = (app_secret or os.environ.get("INSTAGRAM_APP_SECRET", "")).strip()
        self.redirect_uri = (redirect_uri or os.environ.get("INSTAGRAM_REDIRECT_URI", "")).strip()
        self.api_version = (api_version or os.environ.get("INSTAGRAM_API_VERSION", "v25.0")).strip()
        if not self.api_version.startswith("v") or not self.api_version[1:].replace(".", "").isdigit():
            raise ValueError("INSTAGRAM_API_VERSION must look like v25.0")
        self.client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0))
        self._owns_client = http_client is None

    @property
    def provider_name(self) -> str:
        return "instagram"

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def authorization_url(self, *, state: str, scopes: tuple[str, ...] = DEFAULT_SCOPES) -> str:
        if not self.app_id or not self.redirect_uri:
            raise InstagramAPIError("Instagram OAuth app ID and redirect URI are required")
        if not state or len(state) < 32:
            raise ValueError("OAuth state must be a cryptographically random value of at least 32 characters")
        if set(scopes) - set(DEFAULT_SCOPES):
            raise ValueError("Unsupported Instagram permission requested")
        query = urlencode({
            "client_id": self.app_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": ",".join(scopes),
            "state": state,
            "enable_fb_login": "0",
            "force_authentication": "1",
        })
        return f"{AUTH_BASE}?{query}"

    async def exchange_code(self, code: str) -> dict[str, Any]:
        if not self.app_id or not self.app_secret or not self.redirect_uri:
            raise InstagramAPIError("Instagram OAuth app configuration is incomplete")
        if not code:
            raise ValueError("OAuth authorization code is required")
        response = await self.client.post(TOKEN_ENDPOINT, data={
            "client_id": self.app_id,
            "client_secret": self.app_secret,
            "grant_type": "authorization_code",
            "redirect_uri": self.redirect_uri,
            "code": code,
        })
        payload = _json_or_error(response, "Instagram authorization-code exchange failed")
        if not payload.get("access_token") or not payload.get("user_id"):
            raise InstagramAPIError("Instagram token response omitted access_token or user_id")
        return payload

    async def exchange_long_lived_token(self, short_lived_token: str) -> dict[str, Any]:
        if not self.app_secret:
            raise InstagramAPIError("Instagram app secret is not configured")
        response = await self.client.get(
            f"{GRAPH_BASE}/access_token",
            params={
                "grant_type": "ig_exchange_token",
                "client_secret": self.app_secret,
                "access_token": short_lived_token,
            },
        )
        payload = _json_or_error(response, "Instagram long-lived token exchange failed")
        if not payload.get("access_token"):
            raise InstagramAPIError("Instagram long-lived token response omitted access_token")
        return payload

    async def refresh_long_lived_token(self, access_token: str) -> dict[str, Any]:
        response = await self.client.get(
            f"{GRAPH_BASE}/refresh_access_token",
            params={"grant_type": "ig_refresh_token", "access_token": access_token},
        )
        payload = _json_or_error(response, "Instagram access-token refresh failed")
        if not payload.get("access_token"):
            raise InstagramAPIError("Instagram refresh response omitted access_token")
        return payload

    async def validate_account(self, access_token: str) -> ProviderAccount:
        response = await self.client.get(
            f"{GRAPH_BASE}/{self.api_version}/me",
            params={"fields": "user_id,username", "access_token": access_token},
        )
        payload = _json_or_error(response, "Instagram account validation failed")
        account_id = str(payload.get("user_id") or payload.get("id") or "")
        if not account_id:
            raise InstagramAPIError("Instagram did not return a professional account ID")
        return ProviderAccount(
            provider="instagram",
            provider_account_id=account_id,
            display_name=str(payload.get("username") or account_id),
            health=AccountHealth.CONNECTED,
            scopes=DEFAULT_SCOPES,
        )

    async def create_reel_container(
        self, *, access_token: str, account_id: str, video_url: str,
        caption: str = "", share_to_feed: bool = True,
    ) -> str:
        _validate_public_media_url(video_url)
        response = await self.client.post(
            f"{GRAPH_BASE}/{self.api_version}/{account_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption[:2200],
                "share_to_feed": str(bool(share_to_feed)).lower(),
                "access_token": access_token,
            },
        )
        payload = _json_or_error(response, "Instagram Reel container creation failed")
        container_id = str(payload.get("id") or "")
        if not container_id:
            raise InstagramAPIError("Instagram did not return a media container ID")
        return container_id

    async def get_container_status(self, *, access_token: str, container_id: str) -> ProviderOperation:
        response = await self.client.get(
            f"{GRAPH_BASE}/{self.api_version}/{container_id}",
            params={"fields": "status_code,status", "access_token": access_token},
        )
        payload = _json_or_error(response, "Instagram media processing status lookup failed")
        status_code = str(payload.get("status_code") or "").upper()
        if status_code == "FINISHED":
            status = PublishStatus.PROCESSING
        elif status_code in {"IN_PROGRESS", "PUBLISHED"}:
            status = PublishStatus.PROCESSING if status_code == "IN_PROGRESS" else PublishStatus.PUBLISHED
        elif status_code == "ERROR":
            status = PublishStatus.FAILED
        else:
            status = PublishStatus.NEEDS_REVIEW
        return ProviderOperation(
            operation_id=container_id,
            status=status,
            error_code=status_code if status == PublishStatus.FAILED else None,
            provider_status=status_code or None,
        )

    async def publish_container(
        self, *, access_token: str, account_id: str, container_id: str,
    ) -> str:
        # Only call after the container reports FINISHED. Callers persist this
        # transition before invoking the non-idempotent publish endpoint.
        response = await self.client.post(
            f"{GRAPH_BASE}/{self.api_version}/{account_id}/media_publish",
            data={"creation_id": container_id, "access_token": access_token},
        )
        payload = _json_or_error(response, "Instagram media publish failed")
        media_id = str(payload.get("id") or "")
        if not media_id:
            raise InstagramAPIError("Instagram publish response omitted media ID")
        return media_id

def _json_or_error(response: httpx.Response, message: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise InstagramAPIError(message, status_code=response.status_code) from exc
    if response.is_error or (isinstance(payload, dict) and payload.get("error")):
        error = payload.get("error") if isinstance(payload, dict) else {}
        if not isinstance(error, dict):
            error = {}
        code = str(error.get("code") or error.get("type") or "")[:100] or None
        raise InstagramAPIError(
            message, status_code=response.status_code, error_code=code,
        )
    if not isinstance(payload, dict):
        raise InstagramAPIError(message, status_code=response.status_code)
    return payload


def _validate_public_media_url(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Instagram media URL must be a public HTTPS URL")
    host = parsed.hostname.lower().rstrip(".")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local"):
        raise ValueError("Instagram media URL must not target a local host")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return
    if not address.is_global:
        raise ValueError("Instagram media URL must not target a private or reserved IP")
