"""Versioned migrations for native-social tables, isolated from cloud/Alembic."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import Base, OAuthState


async def _initial_schema(connection: AsyncConnection) -> None:
    """Create the initial native-social schema; future changes use explicit migrations."""
    await connection.run_sync(Base.metadata.create_all)


async def _oauth_state_schema(connection: AsyncConnection) -> None:
    await OAuthState.__table__.create(connection, checkfirst=True)


async def _token_refresh_lifecycle(connection: AsyncConnection) -> None:
    """Add refresh leasing and health metadata without rewriting credentials."""
    await connection.execute(text(
        "ALTER TABLE native_social_credentials "
        "ADD COLUMN IF NOT EXISTS refresh_lease_until TIMESTAMPTZ NULL, "
        "ADD COLUMN IF NOT EXISTS last_refresh_attempt_at TIMESTAMPTZ NULL, "
        "ADD COLUMN IF NOT EXISTS last_refreshed_at TIMESTAMPTZ NULL, "
        "ADD COLUMN IF NOT EXISTS last_error_code VARCHAR(64) NULL"
    ))


MIGRATIONS = (
    (1, _initial_schema),
    (2, _oauth_state_schema),
    (3, _token_refresh_lifecycle),
    (4, _publish_worker_leases),
)


async def _publish_worker_leases(connection: AsyncConnection) -> None:
    """Add fenced worker leases for safe recovery of long-running jobs."""
    await connection.execute(text(
        "ALTER TABLE native_publish_targets "
        "ADD COLUMN IF NOT EXISTS worker_lease_token VARCHAR(64) NULL, "
        "ADD COLUMN IF NOT EXISTS worker_lease_until TIMESTAMPTZ NULL"
    ))
    await connection.execute(text(
        "CREATE INDEX IF NOT EXISTS ix_native_publish_targets_worker_lease "
        "ON native_publish_targets (provider, status, worker_lease_until)"
    ))


async def apply_native_social_migrations(connection: AsyncConnection) -> None:
    """Apply ordered migrations once, serialized across application instances."""
    await connection.execute(text(
        "CREATE TABLE IF NOT EXISTS native_social_schema_migrations ("
        "version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"
    ))
    # PostgreSQL transaction-scoped advisory lock prevents two replicas racing
    # to apply the same DDL during a rolling deploy.
    await connection.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"),
                             {"lock_id": 728194630221})
    result = await connection.execute(text(
        "SELECT version FROM native_social_schema_migrations"
    ))
    applied = {int(row[0]) for row in result.fetchall()}
    known_versions = {version for version, _ in MIGRATIONS}
    unknown_versions = applied - known_versions
    if unknown_versions:
        raise RuntimeError(f"Database schema is newer than this application: {sorted(unknown_versions)}")
    for version, migration in MIGRATIONS:
        if version in applied:
            continue
        await migration(connection)
        await connection.execute(text(
            "INSERT INTO native_social_schema_migrations (version) VALUES (:version)"
        ), {"version": version})
