"""Scheduled refresh of Instagram long-lived user access tokens."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update

from .crypto import KMSCredentialCipher
from .instagram import InstagramAPI, InstagramAPIError
from .models import SocialConnection, SocialCredential
from .persistence import native_social_session

REFRESH_WINDOW = timedelta(days=14)
RETRY_INTERVAL = timedelta(hours=6)
LEASE_DURATION = timedelta(minutes=10)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def refresh_due_instagram_tokens(*, limit: int = 20) -> dict[str, int]:
    """Refresh eligible tokens with a DB lease so app replicas do not race.

    Instagram long-lived tokens are refreshable only after 24 hours and before
    expiry. Expired/invalid credentials require the account owner to reconnect.
    """
    stats = {"refreshed": 0, "reauth_required": 0, "deferred": 0}
    now = _now()
    claimed: list[tuple[object, object, str]] = []

    async with native_social_session() as session:
        async with session.begin():
            expired_ids = select(SocialCredential.connection_id).where(
                SocialCredential.expires_at.is_not(None),
                SocialCredential.expires_at <= now,
            )
            await session.execute(
                update(SocialConnection)
                .where(
                    SocialConnection.id.in_(expired_ids),
                    SocialConnection.provider == "instagram",
                    SocialConnection.health.in_(["connected", "refresh_degraded"]),
                )
                .values(health="reauth_required")
            )
            await session.execute(
                update(SocialCredential)
                .where(
                    SocialCredential.connection_id.in_(expired_ids),
                    SocialCredential.last_error_code.is_(None),
                )
                .values(last_error_code="token_expired")
            )
            result = await session.execute(
                select(SocialCredential, SocialConnection)
                .join(SocialConnection, SocialConnection.id == SocialCredential.connection_id)
                .where(
                    SocialConnection.provider == "instagram",
                    SocialConnection.health.in_(["connected", "refresh_degraded"]),
                    SocialCredential.expires_at.is_not(None),
                    SocialCredential.expires_at > now + timedelta(days=1),
                    SocialCredential.expires_at <= now + REFRESH_WINDOW,
                    (
                        SocialCredential.last_refresh_attempt_at.is_(None)
                        | (SocialCredential.last_refresh_attempt_at < now - RETRY_INTERVAL)
                    ),
                    (
                        SocialCredential.refresh_lease_until.is_(None)
                        | (SocialCredential.refresh_lease_until < now)
                    ),
                )
                .order_by(SocialCredential.expires_at.asc())
                .limit(max(1, min(limit, 100)))
                .with_for_update(skip_locked=True)
            )
            for credential, connection in result.all():
                credential.refresh_lease_until = now + LEASE_DURATION
                credential.last_refresh_attempt_at = now
                claimed.append((credential.connection_id, connection.owner_id, str(connection.id)))

    for connection_id, owner_id, _ in claimed:
        api = InstagramAPI()
        try:
            async with native_social_session() as session:
                connection = await session.scalar(select(SocialConnection).where(
                    SocialConnection.id == connection_id,
                    SocialConnection.owner_id == owner_id,
                    SocialConnection.provider == "instagram",
                ))
                credential = await session.get(SocialCredential, connection_id)
                if connection is None or credential is None:
                    continue
                from .crypto import EncryptedCredentials
                encrypted = EncryptedCredentials(
                    encrypted_data_key=credential.encrypted_data_key,
                    nonce=credential.nonce,
                    ciphertext=credential.ciphertext,
                    kms_key_id=credential.kms_key_id,
                    key_version=credential.key_version,
                )
                tokens = KMSCredentialCipher().decrypt(
                    owner_id=owner_id, connection_id=connection_id,
                    provider="instagram", encrypted=encrypted,
                )
                old_token = tokens.get("access_token")
            if not old_token:
                raise RuntimeError("Instagram access token unavailable")
            refreshed = await api.refresh_long_lived_token(old_token)
            new_token = str(refreshed.get("access_token") or "")
            expires_in = int(refreshed.get("expires_in") or 0)
            if not new_token or expires_in <= 0:
                raise InstagramAPIError("Instagram refresh response was incomplete")
            tokens["access_token"] = new_token
            encrypted_new = KMSCredentialCipher().encrypt(
                owner_id=owner_id, connection_id=connection_id,
                provider="instagram", tokens=tokens,
            )
            async with native_social_session() as session:
                async with session.begin():
                    connection = await session.scalar(select(SocialConnection).where(
                        SocialConnection.id == connection_id,
                        SocialConnection.owner_id == owner_id,
                    ).with_for_update())
                    credential = await session.scalar(select(SocialCredential).where(
                        SocialCredential.connection_id == connection_id,
                        SocialCredential.owner_id == owner_id,
                    ).with_for_update())
                    if connection is None or credential is None:
                        continue
                    credential.encrypted_data_key = encrypted_new.encrypted_data_key
                    credential.nonce = encrypted_new.nonce
                    credential.ciphertext = encrypted_new.ciphertext
                    credential.kms_key_id = encrypted_new.kms_key_id
                    credential.key_version = encrypted_new.key_version
                    credential.expires_at = _now() + timedelta(seconds=expires_in)
                    credential.last_refreshed_at = _now()
                    credential.last_error_code = None
                    credential.refresh_lease_until = None
                    connection.health = "connected"
                    connection.last_validated_at = _now()
            stats["refreshed"] += 1
        except asyncio.CancelledError:
            raise
        except InstagramAPIError as exc:
            reauth = exc.status_code in {400, 401} or exc.error_code in {"OAuthException", "190"}
            await _record_refresh_failure(connection_id, owner_id, reauth=reauth)
            stats["reauth_required" if reauth else "deferred"] += 1
        except Exception:
            await _record_refresh_failure(connection_id, owner_id, reauth=False)
            stats["deferred"] += 1
        finally:
            await api.aclose()
    return stats


async def _record_refresh_failure(connection_id, owner_id, *, reauth: bool) -> None:
    async with native_social_session() as session:
        async with session.begin():
            connection = await session.scalar(select(SocialConnection).where(
                SocialConnection.id == connection_id,
                SocialConnection.owner_id == owner_id,
            ).with_for_update())
            credential = await session.scalar(select(SocialCredential).where(
                SocialCredential.connection_id == connection_id,
                SocialCredential.owner_id == owner_id,
            ).with_for_update())
            if credential is not None:
                credential.refresh_lease_until = None
                credential.last_error_code = "reauth_required" if reauth else "refresh_failed"
            if connection is not None:
                connection.health = "reauth_required" if reauth else "refresh_degraded"
