"""Mocked HTTP tests for Instagram Login and Reel publishing."""
import asyncio
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from native_social.instagram import InstagramAPI, InstagramAPIError


def test_oauth_authorization_url_requests_only_required_permissions():
    api = InstagramAPI(app_id="app-123", app_secret="secret",
                       redirect_uri="https://example.com/oauth/instagram/callback")
    url = api.authorization_url(state="s" * 48)
    query = parse_qs(urlsplit(url).query)
    assert urlsplit(url).hostname == "www.instagram.com"
    assert query["client_id"] == ["app-123"]
    assert query["scope"] == ["instagram_business_basic,instagram_business_content_publish"]
    assert query["state"] == ["s" * 48]


def test_oauth_code_exchange_and_long_lived_token_exchange():
    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            if request.url.path == "/oauth/access_token":
                assert request.method == "POST"
                assert b"grant_type=authorization_code" in request.content
                return httpx.Response(200, json={"access_token": "short", "user_id": 123})
            assert request.url.path.endswith("/access_token")
            assert request.url.params["grant_type"] == "ig_exchange_token"
            return httpx.Response(200, json={"access_token": "long", "expires_in": 5184000})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api = InstagramAPI(app_id="app", app_secret="secret",
                           redirect_uri="https://example.com/callback", http_client=client)
        short = await api.exchange_code("auth-code")
        long = await api.exchange_long_lived_token(short["access_token"])
        assert short["user_id"] == 123
        assert long["access_token"] == "long"
        await api.aclose()
        await client.aclose()
    asyncio.run(run())


def test_validate_account_returns_professional_account_identity():
    async def run():
        def handler(request):
            assert request.url.path.endswith("/me")
            assert request.url.params["fields"] == "user_id,username"
            return httpx.Response(200, json={"user_id": "1789", "username": "clipstudio"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api = InstagramAPI(http_client=client)
        account = await api.validate_account("access")
        assert account.provider == "instagram"
        assert account.provider_account_id == "1789"
        assert account.display_name == "clipstudio"
        await api.aclose()
        await client.aclose()
    asyncio.run(run())


def test_reel_container_creation_and_processing_status():
    async def run():
        calls = []
        def handler(request):
            calls.append(request)
            if request.method == "POST":
                assert request.url.path.endswith("/1789/media")
                assert request.content
                assert b"media_type=REELS" in request.content
                assert b"video_url=https%3A%2F%2Fcdn.example.com%2Fclip.mp4" in request.content
                return httpx.Response(200, json={"id": "container-1"})
            return httpx.Response(200, json={"id": "container-1", "status_code": "FINISHED"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api = InstagramAPI(http_client=client)
        container = await api.create_reel_container(
            access_token="access", account_id="1789",
            video_url="https://cdn.example.com/clip.mp4", caption="hello",
        )
        status = await api.get_container_status(access_token="access", container_id=container)
        assert container == "container-1"
        assert status.status.value == "processing"
        assert status.provider_status == "FINISHED"
        await api.aclose()
        await client.aclose()
    asyncio.run(run())


def test_publish_container_returns_media_id():
    async def run():
        def handler(request):
            assert request.method == "POST"
            assert request.url.path.endswith("/1789/media_publish")
            assert b"creation_id=container-1" in request.content
            return httpx.Response(200, json={"id": "media-1"})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api = InstagramAPI(http_client=client)
        media_id = await api.publish_container(
            access_token="access", account_id="1789", container_id="container-1",
        )
        assert media_id == "media-1"
        await api.aclose()
        await client.aclose()
    asyncio.run(run())


def test_local_or_non_https_media_urls_are_rejected():
    from native_social.instagram import _validate_public_media_url
    for value in ("http://cdn.example.com/a.mp4", "https://localhost/a.mp4",
                  "https://127.0.0.1/a.mp4", "https://192.168.1.10/a.mp4"):
        with pytest.raises(ValueError):
            _validate_public_media_url(value)


def test_instagram_api_error_does_not_leak_token():
    async def run():
        def handler(request):
            return httpx.Response(400, json={"error": {"message": "bad token", "code": 190}})
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        api = InstagramAPI(http_client=client)
        with pytest.raises(InstagramAPIError) as caught:
            await api.validate_account("sensitive-token")
        assert "sensitive-token" not in str(caught.value)
        await api.aclose()
        await client.aclose()
    asyncio.run(run())
