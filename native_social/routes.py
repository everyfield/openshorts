"""Authenticated Instagram OAuth routes for hosted mode only."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import update
from starlette.responses import RedirectResponse

from cloud.auth import get_current_user_required
from native_social.crypto import KMSCredentialCipher
from native_social.instagram import InstagramAPI, InstagramAPIError
from native_social.models import OAuthState
from native_social.persistence import native_social_session, store_connection_credentials

router = APIRouter(prefix="/api/native-social/instagram", tags=["native-social-instagram"])
STATE_TTL_MINUTES = 10


def _state_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dashboard_redirect(result: str) -> RedirectResponse:
    # Fixed same-origin relative destination; no user-controlled redirect target.
    return RedirectResponse(url=f"/?instagram_connect={result}", status_code=303)


@router.get("/connect")
async def instagram_connect(request: Request):
    user = await get_current_user_required(request)
    raw_state = secrets.token_urlsafe(48)
    digest = _state_digest(raw_state)
    async with native_social_session() as session:
        async with session.begin():
            session.add(OAuthState(
                state_hash=digest,
                owner_id=user.id,
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=STATE_TTL_MINUTES),
            ))
    api = InstagramAPI()
    try:
        url = api.authorization_url(state=raw_state)
    except (ValueError, InstagramAPIError) as exc:
        raise HTTPException(status_code=503, detail="Instagram OAuth is not configured") from exc
    finally:
        await api.aclose()
    return RedirectResponse(url=url, status_code=302)


@router.get("/callback")
async def instagram_callback(request: Request, code: str | None = None,
                             state: str | None = None, error: str | None = None):
    if error or not code or not state or len(state) > 512:
        return _dashboard_redirect("error")
    digest = _state_digest(state)
    owner_id = None
    # Atomic consume prevents replay and works across workers/restarts.
    async with native_social_session() as session:
        async with session.begin():
            result = await session.execute(
                update(OAuthState)
                .where(
                    OAuthState.state_hash == digest,
                    OAuthState.consumed_at.is_(None),
                    OAuthState.expires_at > datetime.now(timezone.utc),
                )
                .values(consumed_at=datetime.now(timezone.utc))
                .returning(OAuthState.owner_id)
            )
            owner_id = result.scalar_one_or_none()
    if owner_id is None:
        return _dashboard_redirect("error")

    api = InstagramAPI()
    try:
        short_token = await api.exchange_code(code)
        long_token = await api.exchange_long_lived_token(short_token["access_token"])
        account = await api.validate_account(long_token["access_token"])
        expires_in = int(long_token.get("expires_in") or 0)
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in) if expires_in > 0 else None
        connection_id = uuid4()
        tokens = {
            "access_token": long_token["access_token"],
            "token_type": str(long_token.get("token_type") or "Bearer"),
        }
        async with native_social_session() as session:
            async with session.begin():
                await store_connection_credentials(
                    session,
                    owner_id=owner_id,
                    connection_id=connection_id,
                    provider="instagram",
                    provider_account_id=account.provider_account_id,
                    display_name=account.display_name,
                    scopes=list(account.scopes),
                    tokens=tokens,
                    cipher=KMSCredentialCipher(),
                    expires_at=expires_at,
                )
    except (InstagramAPIError, ValueError, RuntimeError):
        # Do not put provider errors, codes, or tokens in redirects/logs.
        return _dashboard_redirect("error")
    finally:
        await api.aclose()
    return _dashboard_redirect("success")
