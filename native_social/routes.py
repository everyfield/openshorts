"""Authenticated Instagram OAuth routes for hosted mode only."""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from uuid import UUID
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select, update
from starlette.responses import RedirectResponse

from cloud.auth import get_current_user_required
from cloud.config import settings
from native_social.crypto import KMSCredentialCipher
from native_social.instagram import InstagramAPI, InstagramAPIError
from native_social.models import OAuthState, SocialConnection
from native_social.persistence import native_social_session, store_connection_credentials
from native_social.publishing import (
    create_instagram_publish_job, get_publish_job, process_instagram_publish_job,
)

router = APIRouter(prefix="/api/native-social/instagram", tags=["native-social-instagram"])
STATE_TTL_MINUTES = 10


def _state_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dashboard_redirect(result: str) -> RedirectResponse:
    # Fixed same-origin relative destination; no user-controlled redirect target.
    return RedirectResponse(url=f"{settings.frontend_url}/?instagram_connect={result}#app?tab=settings", status_code=303)


@router.get("/connect")
async def instagram_connect(request: Request):
    user = await get_current_user_required(request)
    raw_state = secrets.token_urlsafe(48)
    digest = _state_digest(raw_state)
    async with native_social_session() as session:
        async with session.begin():
            now = datetime.now(timezone.utc)
            await session.execute(delete(OAuthState).where(
                (OAuthState.expires_at < now) |
                (OAuthState.consumed_at < now - timedelta(days=1))
            ))
            session.add(OAuthState(
                state_hash=digest,
                owner_id=user.id,
                expires_at=now + timedelta(minutes=STATE_TTL_MINUTES),
            ))
    api = InstagramAPI()
    try:
        url = api.authorization_url(state=raw_state)
    except (ValueError, InstagramAPIError) as exc:
        raise HTTPException(status_code=503, detail="Instagram OAuth is not configured") from exc
    finally:
        await api.aclose()
    return JSONResponse({"authorization_url": url})


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


@router.get("/connections")
async def instagram_connections(request: Request):
    user = await get_current_user_required(request)
    async with native_social_session() as session:
        result = await session.execute(
            select(SocialConnection).where(
                SocialConnection.owner_id == user.id,
                SocialConnection.provider == "instagram",
            ).order_by(SocialConnection.created_at.desc())
        )
        rows = result.scalars().all()
        return {
            "connections": [
                {
                    "id": str(row.id),
                    "account_id": row.provider_account_id,
                    "username": row.display_name,
                    "health": row.health,
                    "scopes": list(row.scopes or []),
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "last_validated_at": row.last_validated_at.isoformat() if row.last_validated_at else None,
                }
                for row in rows
            ]
        }


@router.delete("/connections/{connection_id}")
async def instagram_disconnect(connection_id: str, request: Request):
    user = await get_current_user_required(request)
    try:
        from uuid import UUID
        parsed_id = UUID(connection_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Instagram connection not found") from exc
    async with native_social_session() as session:
        async with session.begin():
            result = await session.execute(
                delete(SocialConnection).where(
                    SocialConnection.id == parsed_id,
                    SocialConnection.owner_id == user.id,
                    SocialConnection.provider == "instagram",
                ).returning(SocialConnection.id)
            )
            if result.scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail="Instagram connection not found")
    # The provider authorization remains revocable from Instagram's app settings;
    # deleting this row cascades the encrypted local credential immediately.
    return {"ok": True}



class InstagramPublishBody(BaseModel):
    connection_id: UUID
    clip_id: UUID
    caption: str = Field(default="", max_length=2200)
    idempotency_key: str = Field(min_length=1, max_length=128)


@router.get("/clips")
async def instagram_publishable_clips(request: Request):
    """Return only the signed-in owner's durable clips archived in R2."""
    user = await get_current_user_required(request)
    try:
        from cloud.database import session as cloud_session
        from cloud.models import UserVideo
        from cloud import storage as cloud_storage
        async with cloud_session() as session:
            result = await session.execute(
                select(UserVideo).where(UserVideo.user_id == user.id)
                .order_by(UserVideo.created_at.desc()).limit(100)
            )
            rows = result.scalars().all()
        return {"clips": [{
            "id": str(row.id),
            "job_id": row.job_id,
            "clip_index": row.clip_index,
            "title": row.title or "Short",
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows]}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Clip library is temporarily unavailable") from exc


@router.post("/publish")
async def instagram_publish(body: InstagramPublishBody, request: Request):
    """Queue and advance one Instagram Reel publish request for the signed-in owner."""
    user = await get_current_user_required(request)
    try:
        owner_id = UUID(str(user.id))
        from cloud.database import session as cloud_session
        from cloud.models import UserVideo
        from cloud import storage as cloud_storage
        async with cloud_session() as library:
            clip = await library.scalar(select(UserVideo).where(
                UserVideo.id == body.clip_id,
                UserVideo.user_id == user.id,
            ))
        if clip is None:
            raise HTTPException(status_code=404, detail="Clip not found")
        media_url = cloud_storage.presigned_get(clip.r2_key, expires=24 * 3600)
        job = await create_instagram_publish_job(
            owner_id=owner_id,
            connection_id=body.connection_id,
            media_url=media_url,
            caption=body.caption,
            idempotency_key=body.idempotency_key,
        )
        # Idempotent replay returns the existing job; only queued jobs are advanced.
        if job["status"] == "queued":
            job = await process_instagram_publish_job(
                owner_id=owner_id, job_id=UUID(job["job_id"]),
            )
        return job
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Instagram publishing is not configured") from exc


@router.get("/publishes/{job_id}")
async def instagram_publish_status(job_id: str, request: Request):
    user = await get_current_user_required(request)
    try:
        parsed_id = UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Publish job not found") from exc
    try:
        return await get_publish_job(owner_id=UUID(str(user.id)), job_id=parsed_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Publish job not found") from exc
