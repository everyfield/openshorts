"""Instagram OAuth and publishing routes for hosted and self-hosted modes."""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid5

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy import delete, select, update
from starlette.responses import RedirectResponse

from native_social.crypto import get_credential_cipher
from native_social.local_media import (
    LocalMediaError, list_local_media_files, resolve_signed_media_token,
)
from native_social.instagram import InstagramAPI, InstagramAPIError
from native_social.models import OAuthState, SocialConnection, SocialCredential
from native_social.persistence import native_social_session, store_connection_credentials
from native_social.publishing import (
    create_instagram_publish_job, get_publish_job, list_instagram_publish_jobs,
    reconcile_instagram_publish_job,
)

router = APIRouter(prefix="/api/native-social/instagram", tags=["native-social-instagram"])
STATE_TTL_MINUTES = 10

_LOCAL_OWNER_ID = UUID(os.environ.get(
    "NATIVE_SOCIAL_LOCAL_OWNER_ID",
    "a56c2b0c-6c6d-5a2e-8c7e-8a2a4f4f3d31",
))


def _billing_enabled() -> bool:
    return os.environ.get("BILLING_ENABLED", "").strip().lower() in {"1", "true", "yes"}


async def _owner_id(request: Request) -> UUID:
    if _billing_enabled():
        from cloud.auth import get_current_user_required
        user = await get_current_user_required(request)
        return UUID(str(user.id))
    return _LOCAL_OWNER_ID


def _local_clip_id(owner_id: UUID, relative_path: str) -> UUID:
    return uuid5(owner_id, relative_path)


def _state_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dashboard_redirect(result: str) -> RedirectResponse:
    if _billing_enabled():
        from cloud.config import settings
        base = settings.frontend_url
    else:
        base = os.environ.get("NATIVE_SOCIAL_FRONTEND_URL", "/").strip() or "/"
    if base.startswith("/") and not base.startswith("//"):
        destination = f"{base}?instagram_connect={result}#app?tab=settings"
    else:
        destination = f"{base.rstrip('/')}/?instagram_connect={result}#app?tab=settings"
    return RedirectResponse(url=destination, status_code=303)


@router.get("/media/{token}", include_in_schema=False)
async def instagram_local_media(token: str):
    """Serve a short-lived signed local file URL to Meta's media fetcher."""
    try:
        path = resolve_signed_media_token(token)
    except LocalMediaError as exc:
        raise HTTPException(status_code=404, detail="Media URL is invalid or expired") from exc
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/connect")
async def instagram_connect(request: Request):
    owner_id = await _owner_id(request)
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
                owner_id=owner_id,
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
        if expires_in <= 0:
            raise InstagramAPIError("Instagram long-lived token response omitted expires_in")
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
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
                    cipher=get_credential_cipher(),
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
    owner_id = await _owner_id(request)
    async with native_social_session() as session:
        result = await session.execute(
            select(SocialConnection).where(
                SocialConnection.owner_id == owner_id,
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
    owner_id = await _owner_id(request)
    try:
        from uuid import UUID
        parsed_id = UUID(connection_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=404, detail="Instagram connection not found") from exc
    async with native_social_session() as session:
        async with session.begin():
            connection = await session.scalar(select(SocialConnection).where(
                SocialConnection.id == parsed_id,
                SocialConnection.owner_id == owner_id,
                SocialConnection.provider == "instagram",
            ).with_for_update())
            if connection is None:
                raise HTTPException(status_code=404, detail="Instagram connection not found")
            # Keep the connection row because publish history references it.
            # Remove the encrypted token immediately while preserving history.
            connection.health = "disconnected"
            await session.execute(delete(SocialCredential).where(
                SocialCredential.connection_id == parsed_id,
                SocialCredential.owner_id == owner_id,
            ))
    # Provider authorization remains revocable from Instagram's app settings.
    return {"ok": True}



class InstagramPublishBody(BaseModel):
    connection_id: UUID
    clip_id: UUID
    caption: str = Field(default="", max_length=2200)
    idempotency_key: str = Field(min_length=1, max_length=128)


class InstagramPublishReconcileBody(BaseModel):
    outcome: str = Field(pattern="^(published|not_published)$")
    media_id: str | None = Field(default=None, max_length=255)


@router.get("/clips")
async def instagram_publishable_clips(request: Request):
    """List local MP4 files in self-host mode; hosted mode uses the R2 library."""
    owner_id = await _owner_id(request)
    if not _billing_enabled():
        return {"clips": [{
            "id": str(_local_clip_id(owner_id, relative)),
            "job_id": path.parent.name,
            "clip_index": None,
            "title": path.stem,
            "created_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
        } for relative, path in list_local_media_files(limit=100)]}
    try:
        from cloud.database import session as cloud_session
        from cloud.models import UserVideo
        async with cloud_session() as session:
            result = await session.execute(
                select(UserVideo).where(UserVideo.user_id == owner_id)
                .order_by(UserVideo.created_at.desc()).limit(100)
            )
            rows = result.scalars().all()
        return {"clips": [{
            "id": str(row.id), "job_id": row.job_id, "clip_index": row.clip_index,
            "title": row.title or "Short",
            "created_at": row.created_at.isoformat() if row.created_at else None,
        } for row in rows]}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Clip library is temporarily unavailable") from exc


@router.post("/publish")
async def instagram_publish(body: InstagramPublishBody, request: Request):
    """Queue a Reel using a locally stored MP4 or the hosted R2 clip library."""
    owner_id = await _owner_id(request)
    try:
        if _billing_enabled():
            from cloud.database import session as cloud_session
            from cloud.models import UserVideo
            async with cloud_session() as library:
                clip = await library.scalar(select(UserVideo).where(
                    UserVideo.id == body.clip_id, UserVideo.user_id == owner_id,
                ))
            if clip is None:
                raise HTTPException(status_code=404, detail="Clip not found")
            media_key = clip.r2_key
        else:
            media_key = next((
                relative for relative, _path in list_local_media_files(limit=500)
                if _local_clip_id(owner_id, relative) == body.clip_id
            ), None)
            if media_key is None:
                raise HTTPException(status_code=404, detail="Local clip not found")
        return await create_instagram_publish_job(
            owner_id=owner_id, connection_id=body.connection_id,
            media_key=media_key, caption=body.caption,
            idempotency_key=body.idempotency_key,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="Instagram publishing is not configured") from exc


@router.get("/publishes")
async def instagram_publish_history(request: Request, limit: int = 50):
    owner_id = await _owner_id(request)
    rows = await list_instagram_publish_jobs(
        owner_id=owner_id, limit=max(1, min(limit, 100)),
    )
    return {"publishes": rows}


@router.post("/publishes/{job_id}/reconcile")
async def instagram_reconcile_publish(
    job_id: str, body: InstagramPublishReconcileBody, request: Request,
):
    owner_id = await _owner_id(request)
    try:
        parsed_id = UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Publish job not found") from exc
    try:
        return await reconcile_instagram_publish_job(
            owner_id=owner_id, job_id=parsed_id,
            outcome=body.outcome, media_id=body.media_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Publish job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/publishes/{job_id}")
async def instagram_publish_status(job_id: str, request: Request):
    owner_id = await _owner_id(request)
    try:
        parsed_id = UUID(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Publish job not found") from exc
    try:
        return await get_publish_job(owner_id=owner_id, job_id=parsed_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Publish job not found") from exc
