"""Mocked HTTP tests for the official YouTube Data API transport."""
import json

import httpx
import pytest

import native_social.youtube as youtube
from native_social.types import PublishStatus
from native_social.youtube import YouTubeAPIError, YouTubeDataAPI


@pytest.mark.asyncio
async def test_refresh_access_token_uses_server_side_oauth_credentials():
    def handler(request):
        assert request.url.path == "/token"
        assert request.content
        assert b"refresh_token=refresh-secret" in request.content
        return httpx.Response(200, json={"access_token": "new-access", "expires_in": 3600})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = YouTubeDataAPI(client_id="client", client_secret="secret", http_client=client)
    assert await api.refresh_access_token("refresh-secret") == {
        "access_token": "new-access", "expires_in": 3600,
    }
    await api.aclose()


@pytest.mark.asyncio
async def test_validate_account_uses_mine_true_and_returns_channel_identity():
    def handler(request):
        assert request.url.path.endswith("/channels")
        assert request.url.params["mine"] == "true"
        assert request.headers["Authorization"] == "Bearer access-secret"
        return httpx.Response(200, json={"items": [{
            "id": "UC123", "snippet": {"title": "Test Channel"},
        }]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = YouTubeDataAPI(http_client=client)
    account = await api.validate_account("access-secret")
    assert account.provider_account_id == "UC123"
    assert account.display_name == "Test Channel"
    assert account.health.value == "connected"
    await api.aclose()


@pytest.mark.asyncio
async def test_upload_uses_resumable_protocol_and_private_default(tmp_path, monkeypatch):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"abcdefgh")
    monkeypatch.setattr(youtube, "CHUNK_SIZE", 4)
    received = []

    def handler(request):
        if request.method == "POST":
            assert request.url.params["uploadType"] == "resumable"
            metadata = json.loads(request.content)
            assert metadata["status"]["privacyStatus"] == "private"
            assert request.headers["X-Upload-Content-Length"] == "8"
            return httpx.Response(200, headers={
                "Location": "https://www.googleapis.com/upload/youtube/v3/videos?upload_id=test",
            })
        received.append(request.headers["Content-Range"])
        if len(received) == 1:
            return httpx.Response(308, headers={"Range": "bytes=0-3"})
        return httpx.Response(201, json={"id": "video-123"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = YouTubeDataAPI(http_client=client)
    result = await api.upload_video(
        access_token="access", media_path=media, title="Test clip",
    )
    assert received == ["bytes 0-3/8", "bytes 4-7/8"]
    assert result.operation_id == "video-123"
    assert result.status is PublishStatus.PROCESSING
    await api.aclose()


@pytest.mark.asyncio
async def test_public_upload_is_blocked_until_explicit_audit_gate(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"video")
    client = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: pytest.fail("No HTTP request should be sent")
    ))
    api = YouTubeDataAPI(http_client=client, public_posting_approved=False)
    with pytest.raises(YouTubeAPIError, match="disabled until this API client passes YouTube audit"):
        await api.upload_video(
            access_token="access", media_path=media, title="Test",
            privacy_status="public",
        )
    await api.aclose()


@pytest.mark.asyncio
async def test_status_polling_maps_processing_success_to_published():
    def handler(request):
        assert request.url.path.endswith("/videos")
        assert request.url.params["id"] == "video-123"
        return httpx.Response(200, json={"items": [{
            "id": "video-123",
            "status": {"uploadStatus": "processed"},
            "processingDetails": {"processingStatus": "succeeded"},
        }]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    api = YouTubeDataAPI(http_client=client)
    result = await api.get_publish_status("access", "video-123")
    assert result.status is PublishStatus.PUBLISHED
    await api.aclose()
