"""First-party YouTube Data API v3 transport.

Uses official OAuth token, channels.list, videos.insert resumable upload,
videos.list status, and token revocation endpoints. No browser automation.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from .types import AccountHealth, ProviderAccount, ProviderOperation, PublishStatus


YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"
YOUTUBE_READONLY_SCOPE = "https://www.googleapis.com/auth/youtube.readonly"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"
API_BASE = "https://www.googleapis.com/youtube/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3/videos"
CHUNK_SIZE = 8 * 1024 * 1024  # multiple of 256 KiB


class YouTubeAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, reason: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.reason = reason


class AmbiguousYouTubeUpload(YouTubeAPIError):
    """The upload may have reached YouTube; caller must reconcile, never blindly retry."""


class YouTubeDataAPI:
    def __init__(
        self, *, client_id: str | None = None, client_secret: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        public_posting_approved: bool | None = None,
    ):
        self.client_id = (client_id or os.environ.get("GOOGLE_CLIENT_ID", "")).strip()
        self.client_secret = (client_secret or os.environ.get("GOOGLE_CLIENT_SECRET", "")).strip()
        self.client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0))
        self._owns_client = http_client is None
        if public_posting_approved is None:
            public_posting_approved = os.environ.get(
                "YOUTUBE_PUBLIC_POSTING_APPROVED", ""
            ).lower() in {"1", "true", "yes"}
        self.public_posting_approved = public_posting_approved

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
        if not self.client_id or not self.client_secret:
            raise YouTubeAPIError("Google OAuth client configuration is missing")
        response = await self.client.post(TOKEN_ENDPOINT, data={
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        })
        if response.is_error:
            raise YouTubeAPIError(
                "Google access-token refresh failed",
                status_code=response.status_code,
                reason=_error_reason(response),
            )
        data = response.json()
        if not isinstance(data.get("access_token"), str):
            raise YouTubeAPIError("Google token response did not contain an access token")
        return data

    async def validate_account(self, access_token: str) -> ProviderAccount:
        response = await self.client.get(
            f"{API_BASE}/channels",
            params={"part": "snippet", "mine": "true"},
            headers=_bearer(access_token),
        )
        if response.is_error:
            raise YouTubeAPIError(
                "YouTube channel validation failed",
                status_code=response.status_code,
                reason=_error_reason(response),
            )
        items = response.json().get("items") or []
        if not items:
            raise YouTubeAPIError("No YouTube channel is available for this Google account")
        channel = items[0]
        channel_id = channel.get("id")
        title = (channel.get("snippet") or {}).get("title") or ""
        if not channel_id:
            raise YouTubeAPIError("YouTube did not return a channel ID")
        return ProviderAccount(
            provider="youtube",
            provider_account_id=channel_id,
            display_name=title,
            health=AccountHealth.CONNECTED,
            scopes=(YOUTUBE_UPLOAD_SCOPE, YOUTUBE_READONLY_SCOPE),
        )

    async def upload_video(
        self, *, access_token: str, media_path: str | Path,
        title: str, description: str = "", tags: list[str] | None = None,
        category_id: str = "22", privacy_status: str = "private",
    ) -> ProviderOperation:
        path = Path(media_path)
        if not path.is_file():
            raise YouTubeAPIError("Rendered media file does not exist")
        size = path.stat().st_size
        if size <= 0:
            raise YouTubeAPIError("Rendered media file is empty")
        if size > 256 * 1024 * 1024 * 1024:
            raise YouTubeAPIError("Video exceeds YouTube Data API 256 GB upload limit")
        if privacy_status not in {"private", "unlisted", "public"}:
            raise ValueError("Invalid YouTube privacy status")
        if privacy_status != "private" and not self.public_posting_approved:
            raise YouTubeAPIError(
                "Public/unlisted upload is disabled until this API client passes YouTube audit"
            )

        clean_tags = _normalize_tags(tags or [])
        metadata = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": clean_tags,
                "categoryId": category_id,
            },
            "status": {"privacyStatus": privacy_status},
        }
        try:
            init = await self.client.post(
                UPLOAD_BASE,
                params={"uploadType": "resumable", "part": "snippet,status"},
                headers={
                    **_bearer(access_token),
                    "Content-Type": "application/json; charset=UTF-8",
                    "X-Upload-Content-Length": str(size),
                    "X-Upload-Content-Type": "video/*",
                },
                json=metadata,
            )
        except httpx.HTTPError as exc:
            # The initiation request itself may have reached Google. There is
            # no session URL to reconcile, so a second videos.insert is unsafe.
            raise AmbiguousYouTubeUpload("YouTube upload initiation outcome is unknown") from exc
        if init.is_error:
            raise YouTubeAPIError(
                "YouTube rejected upload initialization",
                status_code=init.status_code,
                reason=_error_reason(init),
            )
        upload_url = init.headers.get("Location")
        parsed_upload_url = urlsplit(upload_url or "")
        if parsed_upload_url.scheme != "https" or parsed_upload_url.hostname != "www.googleapis.com":
            raise YouTubeAPIError("YouTube did not return a valid resumable upload URL")

        try:
            video_id = await self._transfer_resumable(
                access_token=access_token, upload_url=upload_url, path=path, size=size,
            )
        except AmbiguousYouTubeUpload:
            raise
        except httpx.HTTPError as exc:
            raise AmbiguousYouTubeUpload("YouTube upload transfer outcome is unknown") from exc
        return ProviderOperation(
            operation_id=video_id, status=PublishStatus.PROCESSING,
            provider_post_id=video_id,
        )

    async def _transfer_resumable(
        self, *, access_token: str, upload_url: str, path: Path, size: int,
    ) -> str:
        offset = 0
        reconciliations = 0
        no_progress = 0
        with path.open("rb") as media:
            while offset < size:
                media.seek(offset)
                chunk = media.read(min(CHUNK_SIZE, size - offset))
                end = offset + len(chunk) - 1
                try:
                    response = await self.client.put(
                        upload_url,
                        headers={
                            **_bearer(access_token),
                            "Content-Type": "video/*",
                            "Content-Length": str(len(chunk)),
                            "Content-Range": f"bytes {offset}-{end}/{size}",
                        },
                        content=chunk,
                    )
                except httpx.HTTPError:
                    reconciliations += 1
                    if reconciliations > 5:
                        raise AmbiguousYouTubeUpload("YouTube upload could not be reconciled")
                    previous_offset = offset
                    offset, completed_id = await self._query_upload(
                        access_token=access_token, upload_url=upload_url, size=size,
                    )
                    if completed_id:
                        return completed_id
                    no_progress = no_progress + 1 if offset <= previous_offset else 0
                    if no_progress > 3:
                        raise AmbiguousYouTubeUpload("YouTube upload made no resumable progress")
                    continue

                if response.status_code in (200, 201):
                    video_id = (response.json() or {}).get("id")
                    if not video_id:
                        raise AmbiguousYouTubeUpload("YouTube completed upload without returning a video ID")
                    return video_id
                if response.status_code == 308:
                    next_offset = _next_offset(response, size)
                    if next_offset > size:
                        raise AmbiguousYouTubeUpload("YouTube returned an invalid upload offset")
                    no_progress = no_progress + 1 if next_offset <= offset else 0
                    if no_progress > 3:
                        raise AmbiguousYouTubeUpload("YouTube upload made no resumable progress")
                    offset = next_offset
                    continue
                if response.status_code >= 500:
                    reconciliations += 1
                    if reconciliations > 5:
                        raise AmbiguousYouTubeUpload("YouTube upload repeatedly failed during transfer")
                    previous_offset = offset
                    offset, completed_id = await self._query_upload(
                        access_token=access_token, upload_url=upload_url, size=size,
                    )
                    if completed_id:
                        return completed_id
                    no_progress = no_progress + 1 if offset <= previous_offset else 0
                    if no_progress > 3:
                        raise AmbiguousYouTubeUpload("YouTube upload made no resumable progress")
                    continue
                raise YouTubeAPIError(
                    "YouTube rejected an upload chunk",
                    status_code=response.status_code,
                    reason=_error_reason(response),
                )
        _, completed_id = await self._query_upload(
            access_token=access_token, upload_url=upload_url, size=size,
        )
        if completed_id:
            return completed_id
        raise AmbiguousYouTubeUpload("Upload ended without a final YouTube video ID")

    async def _query_upload(
        self, *, access_token: str, upload_url: str, size: int,
    ) -> tuple[int, str | None]:
        response = await self.client.put(
            upload_url,
            headers={
                **_bearer(access_token),
                "Content-Length": "0",
                "Content-Range": f"bytes */{size}",
            },
            content=b"",
        )
        if response.status_code in (200, 201):
            video_id = (response.json() or {}).get("id")
            if not video_id:
                raise AmbiguousYouTubeUpload("Upload status response omitted video ID")
            return size, video_id
        if response.status_code == 308:
            return _next_offset(response, size), None
        raise AmbiguousYouTubeUpload(
            "YouTube resumable upload status could not be confirmed",
            status_code=response.status_code,
            reason=_error_reason(response),
        )

    async def get_publish_status(self, access_token: str, video_id: str) -> ProviderOperation:
        response = await self.client.get(
            f"{API_BASE}/videos",
            params={"part": "status,processingDetails", "id": video_id},
            headers=_bearer(access_token),
        )
        if response.is_error:
            raise YouTubeAPIError(
                "YouTube publication status lookup failed",
                status_code=response.status_code,
                reason=_error_reason(response),
            )
        items = response.json().get("items") or []
        if not items:
            return ProviderOperation(video_id, PublishStatus.NEEDS_REVIEW, provider_post_id=video_id)
        item = items[0]
        upload_status = (item.get("status") or {}).get("uploadStatus", "")
        processing_status = (item.get("processingDetails") or {}).get("processingStatus", "")
        if upload_status in {"failed", "rejected", "deleted"} or processing_status in {"failed", "terminated"}:
            state = PublishStatus.FAILED
        elif processing_status == "succeeded" or upload_status == "processed":
            state = PublishStatus.PUBLISHED
        else:
            state = PublishStatus.PROCESSING
        return ProviderOperation(video_id, state, provider_post_id=video_id)

    async def revoke_token(self, token: str) -> None:
        response = await self.client.post(REVOKE_ENDPOINT, data={"token": token})
        if response.is_error:
            raise YouTubeAPIError(
                "Google OAuth token revocation failed",
                status_code=response.status_code,
                reason=_error_reason(response),
            )


def _bearer(token: str) -> dict[str, str]:
    if not token:
        raise ValueError("Access token is required")
    return {"Authorization": f"Bearer {token}"}


def _next_offset(response: httpx.Response, size: int) -> int:
    value = response.headers.get("Range")
    if not value:
        return 0
    try:
        last_byte = int(value.rsplit("-", 1)[1])
    except (IndexError, ValueError) as exc:
        raise AmbiguousYouTubeUpload("YouTube returned a malformed upload Range header") from exc
    return min(last_byte + 1, size)


def _error_reason(response: httpx.Response) -> str | None:
    try:
        payload = response.json()
        errors = payload.get("error", {}).get("errors") or []
        if errors:
            return str(errors[0].get("reason") or "")[:100] or None
        return str(payload.get("error", {}).get("status") or "")[:100] or None
    except Exception:
        return None


def _normalize_tags(tags: list[str]) -> list[str]:
    if any(not isinstance(tag, str) for tag in tags):
        raise ValueError("YouTube tags must be strings")
    cleaned = [tag.strip() for tag in tags if tag.strip()]
    if sum(len(tag) for tag in cleaned) + max(0, len(cleaned) - 1) > 500:
        raise ValueError("Combined YouTube tag length exceeds 500 characters")
    return cleaned
