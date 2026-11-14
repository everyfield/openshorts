"""Versioned migrations for native-social tables, isolated from cloud/Alembic."""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import Base


async def _initial_schema(connection: AsyncConnection) -> None:
    """Create the initial native-social schema; future changes use explicit migrations."""
    await connection.run_sync(Base.metadata.create_all)


MIGRATIONS = (
    (1, _initial_schema),
)


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
    for version, migration in MIGRATIONS:
        if version in applied:
            continue
        await migration(connection)
        await connection.execute(text(
            "INSERT INTO native_social_schema_migrations (version) VALUES (:version)"
        ), {"version": version})
