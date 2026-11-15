"""Core-owned persistence bootstrap and owner-scoped credential repository."""
from __future__ import annotations

import os
from datetime import datetime
from typing import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .crypto import EncryptedCredentials, KMSCredentialCipher
from .models import SocialConnection, SocialCredential
from .migrations import apply_native_social_migrations

_engine = None
_sessions = None


async def init_native_social_database(database_url: str | None = None) -> None:
    """Initialize only native_social tables on the hosted PostgreSQL database."""
    global _engine, _sessions
    if _engine is not None:
        return
    url = (database_url or os.environ.get("DATABASE_URL", "")).strip()
    if not url:
        raise RuntimeError("DATABASE_URL is required for native social persistence")
    _engine = create_async_engine(url, pool_pre_ping=True)
    _sessions = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    async with _engine.begin() as connection:
        await apply_native_social_migrations(connection)


def native_social_session() -> AsyncSession:
    if _sessions is None:
        raise RuntimeError("Native social database is not initialized")
    return _sessions()


async def store_connection_credentials(
    session: AsyncSession, *, owner_id: UUID, connection_id: UUID,
    provider: str, provider_account_id: str, display_name: str,
    scopes: list[str], tokens: Mapping[str, str],
    cipher: KMSCredentialCipher, expires_at: datetime | None = None,
) -> SocialConnection:
    """Upsert account metadata and encrypted credentials within caller transaction."""
    provider = provider.strip().lower()
    if provider not in {"youtube", "instagram", "tiktok"}:
        raise ValueError("Unsupported social provider")
    encrypted = cipher.encrypt(
        owner_id=owner_id, connection_id=connection_id, provider=provider, tokens=tokens,
    )
    result = await session.execute(select(SocialConnection).where(
        SocialConnection.owner_id == owner_id,
        SocialConnection.provider == provider,
        SocialConnection.provider_account_id == provider_account_id,
    ))
    connection = result.scalar_one_or_none()
    if connection is None:
        connection = SocialConnection(
            id=connection_id, owner_id=owner_id, provider=provider,
            provider_account_id=provider_account_id, display_name=display_name,
            health="connected", scopes=list(scopes),
        )
        session.add(connection)
        await session.flush()
    elif connection.id != connection_id:
        connection_id = connection.id
        encrypted = cipher.encrypt(
            owner_id=owner_id, connection_id=connection_id, provider=provider, tokens=tokens,
        )
        connection.display_name = display_name
        connection.health = "connected"
        connection.scopes = list(scopes)

    credential = await session.get(SocialCredential, connection_id)
    values = dict(
        owner_id=owner_id,
        encrypted_data_key=encrypted.encrypted_data_key,
        nonce=encrypted.nonce,
        ciphertext=encrypted.ciphertext,
        kms_key_id=encrypted.kms_key_id,
        key_version=encrypted.key_version,
        expires_at=expires_at,
        refresh_lease_until=None,
        last_refresh_attempt_at=None,
        last_refreshed_at=None,
        last_error_code=None,
        scopes=list(scopes),
    )
    if credential is None:
        session.add(SocialCredential(connection_id=connection_id, **values))
    else:
        for key, value in values.items():
            setattr(credential, key, value)
    return connection


async def load_connection_credentials(
    session: AsyncSession, *, owner_id: UUID, connection_id: UUID,
    cipher: KMSCredentialCipher,
) -> dict[str, str | None]:
    """Load/decrypt only when both the connection and credential belong to owner."""
    result = await session.execute(select(SocialConnection).where(
        SocialConnection.id == connection_id,
        SocialConnection.owner_id == owner_id,
    ))
    connection = result.scalar_one_or_none()
    if connection is None:
        raise LookupError("Social connection not found")
    credential_result = await session.execute(select(SocialCredential).where(
        SocialCredential.connection_id == connection_id,
        SocialCredential.owner_id == owner_id,
    ))
    credential = credential_result.scalar_one_or_none()
    if credential is None:
        raise LookupError("Social credentials not found")
    encrypted = EncryptedCredentials(
        encrypted_data_key=credential.encrypted_data_key,
        nonce=credential.nonce,
        ciphertext=credential.ciphertext,
        kms_key_id=credential.kms_key_id,
        key_version=credential.key_version,
    )
    return cipher.decrypt(
        owner_id=owner_id, connection_id=connection_id,
        provider=connection.provider, encrypted=encrypted,
    )


async def close_native_social_database() -> None:
    """Dispose the isolated native-social connection pool on application shutdown."""
    global _engine, _sessions
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessions = None
