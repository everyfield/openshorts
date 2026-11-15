"""Durable, single-target Instagram Reels publishing orchestration.

The database is the source of truth for each phase. The non-idempotent
media_publish request is preceded by a committed publishing claim; ambiguous
outcomes are marked needs_review and are never automatically retried.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .crypto import KMSCredentialCipher
from .instagram import InstagramAPI, InstagramAPIError
from .models import PublishJob, PublishTarget, SocialConnection
from .persistence import load_connection_credentials, native_social_session


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_instagram_publish_job(
    *, owner_id: UUID, connection_id: UUID, media_url: str,
    caption: str, idempotency_key: str,
) -> dict:
    if not media_url or len(media_url) > 4096:
        raise ValueError("A valid public HTTPS media URL is required")
    if len(caption) > 2200:
        raise ValueError("Instagram captions must be at most 2,200 characters")
    if not idempotency_key or len(idempotency_key) > 128:
        raise ValueError("An idempotency key of 1-128 characters is required")

    async with native_social_session() as session:
        async with session.begin():
            connection = await session.scalar(select(SocialConnection).where(
                SocialConnection.id == connection_id,
                SocialConnection.owner_id == owner_id,
                SocialConnection.provider == "instagram",
                SocialConnection.health == "connected",
            ))
            if connection is None:
                raise LookupError("Instagram connection not found")
            existing = await session.scalar(select(PublishJob).where(
                PublishJob.owner_id == owner_id,
                PublishJob.idempotency_key == idempotency_key,
            ))
            if existing is not None:
                target = await session.scalar(select(PublishTarget).where(
                    PublishTarget.publish_job_id == existing.id,
                    PublishTarget.connection_id == connection_id,
                ))
                if (existing.media_reference != media_url or existing.caption != caption
                        or target is None):
                    raise ValueError("Idempotency key was already used for a different publish request")
                return _job_result(existing, target)
            job = PublishJob(
                id=uuid4(), owner_id=owner_id, media_reference=media_url,
                caption=caption, status="queued", idempotency_key=idempotency_key,
            )
            session.add(job)
            await session.flush()
            target = PublishTarget(
                id=uuid4(), publish_job_id=job.id, connection_id=connection_id,
                provider="instagram", status="queued",
            )
            session.add(target)
            await session.flush()
            return _job_result(job, target)


def _job_result(job: PublishJob, target: PublishTarget | None) -> dict:
    return {
        "job_id": str(job.id),
        "status": target.status if target else job.status,
        "container_id": target.provider_operation_id if target else None,
        "media_id": target.provider_post_id if target else None,
        "error_code": target.error_code if target else None,
    }


async def process_instagram_publish_job(*, owner_id: UUID, job_id: UUID) -> dict:
    """Advance one queued Instagram target. Safe to invoke after process restart."""
    async with native_social_session() as session:
        async with session.begin():
            job = await session.scalar(select(PublishJob).where(
                PublishJob.id == job_id, PublishJob.owner_id == owner_id,
            ).with_for_update())
            if job is None:
                raise LookupError("Publish job not found")
            target = await session.scalar(select(PublishTarget).where(
                PublishTarget.publish_job_id == job.id,
                PublishTarget.provider == "instagram",
            ).with_for_update())
            if target is None:
                raise LookupError("Instagram publish target not found")
            if target.status in {"published", "needs_review", "failed"}:
                return _job_result(job, target)
            if target.status == "publishing":
                # A prior worker may have transmitted media_publish before dying.
                target.status = "needs_review"
                target.error_code = "publish_outcome_unknown"
                target.error_message = "Publish outcome is unknown; reconcile in Instagram before retrying."
                job.status = "needs_review"
                return _job_result(job, target)
            if target.status == "processing":
                # A second request/worker must never create another container.
                # Stale processing jobs require explicit operator reconciliation.
                return _job_result(job, target)
            if target.status not in {"queued", "pending"}:
                raise ValueError("Publish target is not in a runnable state")
            connection = await session.get(SocialConnection, target.connection_id)
            if connection is None or connection.owner_id != owner_id or connection.provider != "instagram":
                raise LookupError("Instagram connection not found")
            credentials = await load_connection_credentials(
                session, owner_id=owner_id, connection_id=connection.id,
                cipher=KMSCredentialCipher(),
            )
            access_token = credentials["access_token"]
            if not access_token:
                raise RuntimeError("Instagram access token unavailable")
            # Persist a worker lease/attempt before making provider requests.
            target.attempt_count += 1
            target.started_at = target.started_at or _now()
            target.status = "processing"
            job.status = "processing"
            media_url, caption = job.media_reference, job.caption
            account_id = connection.provider_account_id
            container_id = target.provider_operation_id

    api = InstagramAPI()
    try:
        if not container_id:
            container_id = await api.create_reel_container(
                access_token=access_token, account_id=account_id,
                video_url=media_url, caption=caption,
            )
            async with native_social_session() as session:
                async with session.begin():
                    target = await session.scalar(select(PublishTarget).join(
                        PublishJob, PublishTarget.publish_job_id == PublishJob.id
                    ).where(PublishJob.id == job_id, PublishJob.owner_id == owner_id).with_for_update())
                    if target is None:
                        raise LookupError("Publish target disappeared")
                    target.provider_operation_id = container_id
                    target.status = "processing"

        max_polls = max(1, min(int(os.environ.get("INSTAGRAM_PROCESSING_MAX_POLLS", "24")), 60))
        poll_seconds = max(1, min(int(os.environ.get("INSTAGRAM_PROCESSING_POLL_SECONDS", "5")), 30))
        finished = False
        for poll in range(max_polls):
            operation = await api.get_container_status(
                access_token=access_token, container_id=container_id,
            )
            if operation.error_code:
                await _set_target(owner_id, job_id, "failed", error_code=operation.error_code)
                return await get_publish_job(owner_id=owner_id, job_id=job_id)
            if operation.status.value == "processing":
                # The adapter uses PROCESSING for both IN_PROGRESS and FINISHED.
                # Query raw status_code to distinguish them before publishing.
                status_response = await api.client.get(
                    f"https://graph.instagram.com/{api.api_version}/{container_id}",
                    params={"fields": "status_code", "access_token": access_token},
                )
                payload = status_response.json()
                if status_response.is_error or not isinstance(payload, dict):
                    await _set_target(owner_id, job_id, "needs_review", error_code="status_lookup_failed")
                    return await get_publish_job(owner_id=owner_id, job_id=job_id)
                code = str(payload.get("status_code") or "").upper()
                if code == "FINISHED":
                    finished = True
                    break
                if code not in {"IN_PROGRESS", "PUBLISHED"}:
                    await _set_target(owner_id, job_id, "needs_review", error_code="unexpected_container_status")
                    return await get_publish_job(owner_id=owner_id, job_id=job_id)
            else:
                await _set_target(owner_id, job_id, "failed", error_code="container_processing_failed")
                return await get_publish_job(owner_id=owner_id, job_id=job_id)
            if poll + 1 < max_polls:
                await asyncio.sleep(poll_seconds)

        if not finished:
            await _set_target(owner_id, job_id, "needs_review", error_code="processing_timeout")
            return await get_publish_job(owner_id=owner_id, job_id=job_id)

        # Commit the one-shot publish claim BEFORE the network request. A crash
        # after this point is ambiguous and is deliberately not auto-retried.
        async with native_social_session() as session:
            async with session.begin():
                target = await session.scalar(select(PublishTarget).join(
                    PublishJob, PublishTarget.publish_job_id == PublishJob.id
                ).where(PublishJob.id == job_id, PublishJob.owner_id == owner_id).with_for_update())
                if target is None:
                    raise LookupError("Publish target not found")
                if target.status == "publishing":
                    target.status = "needs_review"
                    target.error_code = "concurrent_publish_claim"
                    target.error_message = "Another worker may have submitted this Reel."
                    return _job_result(await session.get(PublishJob, job_id), target)
                if target.status != "processing":
                    return _job_result(await session.get(PublishJob, job_id), target)
                target.status = "publishing"
                target.error_code = None
                target.error_message = None
                target.updated_at = _now()

        try:
            media_id = await api.publish_container(
                access_token=access_token, account_id=account_id,
                container_id=container_id,
            )
        except Exception:
            # The request may have reached Meta. Do not retry automatically.
            await _set_target(owner_id, job_id, "needs_review", error_code="publish_outcome_unknown")
            return await get_publish_job(owner_id=owner_id, job_id=job_id)

        async with native_social_session() as session:
            async with session.begin():
                job = await session.scalar(select(PublishJob).where(
                    PublishJob.id == job_id, PublishJob.owner_id == owner_id,
                ).with_for_update())
                target = await session.scalar(select(PublishTarget).where(
                    PublishTarget.publish_job_id == job_id,
                    PublishTarget.provider == "instagram",
                ).with_for_update())
                if job is None or target is None:
                    raise LookupError("Publish job not found")
                target.provider_post_id = media_id
                target.status = "published"
                target.completed_at = _now()
                job.status = "published"
        return await get_publish_job(owner_id=owner_id, job_id=job_id)
    except (InstagramAPIError, ValueError):
        # Container creation/status errors are safe to report as failed unless
        # the one-shot publish claim has already been committed.
        current = await get_publish_job(owner_id=owner_id, job_id=job_id)
        if current["status"] == "publishing":
            await _set_target(owner_id, job_id, "needs_review", error_code="publish_outcome_unknown")
        else:
            await _set_target(owner_id, job_id, "failed", error_code="instagram_api_error")
        return await get_publish_job(owner_id=owner_id, job_id=job_id)
    finally:
        await api.aclose()


async def _set_target(owner_id: UUID, job_id: UUID, status: str, *, error_code: str | None = None) -> None:
    async with native_social_session() as session:
        async with session.begin():
            job = await session.scalar(select(PublishJob).where(
                PublishJob.id == job_id, PublishJob.owner_id == owner_id,
            ).with_for_update())
            if job is None:
                return
            target = await session.scalar(select(PublishTarget).where(
                PublishTarget.publish_job_id == job_id,
                PublishTarget.provider == "instagram",
            ).with_for_update())
            if target is None:
                return
            target.status = status
            target.error_code = error_code
            target.error_message = None
            if status in {"published", "failed", "needs_review"}:
                target.completed_at = _now()
            job.status = status


async def get_publish_job(*, owner_id: UUID, job_id: UUID) -> dict:
    async with native_social_session() as session:
        job = await session.scalar(select(PublishJob).where(
            PublishJob.id == job_id, PublishJob.owner_id == owner_id,
        ))
        if job is None:
            raise LookupError("Publish job not found")
        target = await session.scalar(select(PublishTarget).where(
            PublishTarget.publish_job_id == job_id,
            PublishTarget.provider == "instagram",
        ))
        return _job_result(job, target)
