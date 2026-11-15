"""Durable, single-target Instagram Reels publishing orchestration.

The database is the source of truth for each phase. The non-idempotent
media_publish request is preceded by a committed publishing claim; ambiguous
outcomes are marked needs_review and are never automatically retried.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, update
import httpx

from .crypto import KMSCredentialCipher
from .instagram import InstagramAPI, InstagramAPIError, _validate_public_media_url
from .models import PublishJob, PublishTarget, SocialConnection
from .persistence import load_connection_credentials, native_social_session


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def create_instagram_publish_job(
    *, owner_id: UUID, connection_id: UUID, media_key: str,
    caption: str, idempotency_key: str,
) -> dict:
    expected_prefix = f"users/{owner_id}/"
    if (not media_key or len(media_key) > 1024 or not media_key.startswith(expected_prefix)
            or ".." in media_key or "\\" in media_key):
        raise ValueError("A valid owner-scoped R2 clip key is required")
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
                SocialConnection.health.in_(["connected", "refresh_degraded"]),
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
                if (existing.media_reference != media_key or existing.caption != caption
                        or target is None):
                    raise ValueError("Idempotency key was already used for a different publish request")
                return _job_result(existing, target)
            job = PublishJob(
                id=uuid4(), owner_id=owner_id, media_reference=media_key,
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
            # Persist the processing claim/attempt before making provider requests.
            target.attempt_count += 1
            target.started_at = _now()
            target.status = "processing"
            job.status = "processing"
            media_key, caption = job.media_reference, job.caption
            account_id = connection.provider_account_id
            container_id = target.provider_operation_id

    api = InstagramAPI()
    try:
        try:
            from cloud import storage as cloud_storage
            media_url = cloud_storage.presigned_get(media_key, expires=24 * 3600)
            _validate_public_media_url(media_url)
        except Exception:
            await _set_target(owner_id, job_id, "failed", error_code="media_storage_unavailable")
            return await get_publish_job(owner_id=owner_id, job_id=job_id)
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
            if operation.provider_status == "FINISHED":
                finished = True
                break
            if operation.provider_status == "IN_PROGRESS":
                pass
            elif operation.provider_status == "ERROR":
                await _set_target(owner_id, job_id, "failed", error_code="container_processing_failed")
                return await get_publish_job(owner_id=owner_id, job_id=job_id)
            else:
                await _set_target(owner_id, job_id, "needs_review", error_code="unexpected_container_status")
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
    except httpx.HTTPError:
        # A transport timeout may occur after Meta received the request. For
        # container creation or publishing, never assume that nothing happened.
        await _set_target(owner_id, job_id, "needs_review", error_code="provider_transport_outcome_unknown")
        return await get_publish_job(owner_id=owner_id, job_id=job_id)
    except (InstagramAPIError, ValueError):
        current = await get_publish_job(owner_id=owner_id, job_id=job_id)
        if current["status"] in {"publishing", "processing"}:
            await _set_target(owner_id, job_id, "needs_review", error_code="provider_operation_outcome_unknown")
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


async def list_instagram_publish_jobs(*, owner_id: UUID, limit: int = 50) -> list[dict]:
    """Return the authenticated owner's newest Instagram publish history."""
    async with native_social_session() as session:
        result = await session.execute(
            select(PublishJob, PublishTarget, SocialConnection.display_name)
            .join(PublishTarget, PublishTarget.publish_job_id == PublishJob.id)
            .join(SocialConnection, SocialConnection.id == PublishTarget.connection_id)
            .where(PublishJob.owner_id == owner_id, PublishTarget.provider == "instagram")
            .order_by(PublishJob.created_at.desc())
            .limit(max(1, min(limit, 100)))
        )
        rows = result.all()
        return [{
            **_job_result(job, target),
            "caption": job.caption,
            "account": account_name,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "completed_at": target.completed_at.isoformat() if target.completed_at else None,
        } for job, target, account_name in rows]


async def reconcile_instagram_publish_job(
    *, owner_id: UUID, job_id: UUID, outcome: str, media_id: str | None = None,
) -> dict:
    """Record an owner's manual verification; never initiates a provider publish."""
    if outcome not in {"published", "not_published"}:
        raise ValueError("Outcome must be published or not_published")
    if outcome == "published":
        if media_id is not None and (
            not media_id or len(media_id) > 255 or any(ch.isspace() for ch in media_id)
        ):
            raise ValueError("A valid Instagram media ID is required when supplied")
    elif media_id:
        raise ValueError("Media ID must be omitted when confirming not published")

    async with native_social_session() as session:
        async with session.begin():
            job = await session.scalar(select(PublishJob).where(
                PublishJob.id == job_id, PublishJob.owner_id == owner_id,
            ).with_for_update())
            if job is None:
                raise LookupError("Publish job not found")
            target = await session.scalar(select(PublishTarget).where(
                PublishTarget.publish_job_id == job_id,
                PublishTarget.provider == "instagram",
            ).with_for_update())
            if target is None:
                raise LookupError("Instagram publish target not found")
            if target.status != "needs_review":
                raise ValueError("Only jobs requiring review can be reconciled")
            if outcome == "published":
                target.provider_post_id = media_id
                target.status = "published"
                target.error_code = None
                target.error_message = "Manually confirmed in Instagram."
            else:
                target.status = "not_published"
                target.error_code = "manually_confirmed_not_published"
                target.error_message = "Owner confirmed this Reel is not present in Instagram. Create a new publish request if needed."
            target.completed_at = _now()
            job.status = target.status
        return await get_publish_job(owner_id=owner_id, job_id=job_id)



async def recover_stale_instagram_publish_jobs(*, stale_after_seconds: int = 900) -> int:
    """Requeue interrupted pre-publish work; never retry an ambiguous publish."""
    cutoff = _now() - timedelta(seconds=stale_after_seconds)
    async with native_social_session() as session:
        async with session.begin():
            stale = select(PublishTarget.publish_job_id).where(
                PublishTarget.provider == "instagram",
                PublishTarget.status == "processing",
                PublishTarget.started_at < cutoff,
            )
            result = await session.execute(
                update(PublishTarget)
                .where(PublishTarget.publish_job_id.in_(stale))
                .values(status="queued", error_code="worker_recovered", error_message=None)
                .returning(PublishTarget.publish_job_id)
            )
            recovered_ids = list(result.scalars().all())
            if recovered_ids:
                await session.execute(
                    update(PublishJob).where(PublishJob.id.in_(recovered_ids))
                    .values(status="queued")
                )
            ambiguous = select(PublishTarget.publish_job_id).where(
                PublishTarget.provider == "instagram",
                PublishTarget.status == "publishing",
            )
            ambiguous_result = await session.execute(
                update(PublishTarget)
                .where(PublishTarget.publish_job_id.in_(ambiguous))
                .values(
                    status="needs_review",
                    error_code="publish_outcome_unknown",
                    error_message="Worker restarted during publish; reconcile in Instagram before retrying.",
                    completed_at=_now(),
                )
                .returning(PublishTarget.publish_job_id)
            )
            ambiguous_ids = list(ambiguous_result.scalars().all())
            if ambiguous_ids:
                await session.execute(
                    update(PublishJob).where(PublishJob.id.in_(ambiguous_ids))
                    .values(status="needs_review")
                )
            return len(recovered_ids)


async def run_instagram_publish_worker(*, poll_interval_seconds: float = 3.0) -> None:
    """Poll publish jobs and refresh due Instagram credentials."""
    last_token_refresh = 0.0
    while True:
        try:
            loop = asyncio.get_running_loop()
            if loop.time() - last_token_refresh >= 300:
                from .token_refresh import refresh_due_instagram_tokens
                await refresh_due_instagram_tokens()
                last_token_refresh = loop.time()
            async with native_social_session() as session:
                result = await session.execute(
                    select(PublishJob.owner_id, PublishJob.id)
                    .join(PublishTarget, PublishTarget.publish_job_id == PublishJob.id)
                    .where(
                        PublishTarget.provider == "instagram",
                        PublishTarget.status == "queued",
                    )
                    .order_by(PublishJob.created_at.asc())
                    .limit(10)
                )
                pending = list(result.all())
            if not pending:
                await asyncio.sleep(poll_interval_seconds)
                continue
            for owner_id, job_id in pending:
                try:
                    await process_instagram_publish_job(owner_id=owner_id, job_id=job_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Keep the worker alive; the persisted target state remains
                    # available for later reconciliation/recovery.
                    await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(poll_interval_seconds)
