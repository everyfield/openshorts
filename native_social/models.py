"""Independent SQLAlchemy metadata for hosted native social publishing.

This module deliberately does not import the separately licensed cloud models.
"""
from __future__ import annotations

import uuid
from sqlalchemy import (
    Column, DateTime, ForeignKey, Integer, LargeBinary, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base

Base = declarative_base()


def _uuid():
    return uuid.uuid4()


class SocialConnection(Base):
    __tablename__ = "native_social_connections"
    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    provider = Column(String(16), nullable=False)
    provider_account_id = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=False, default="")
    health = Column(String(32), nullable=False, default="connected")
    scopes = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    last_validated_at = Column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        UniqueConstraint("owner_id", "provider", "provider_account_id",
                         name="uq_native_social_owner_provider_account"),
    )


class SocialCredential(Base):
    __tablename__ = "native_social_credentials"
    connection_id = Column(
        UUID(as_uuid=True), ForeignKey("native_social_connections.id", ondelete="CASCADE"),
        primary_key=True,
    )
    owner_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    encrypted_data_key = Column(LargeBinary, nullable=False)
    nonce = Column(LargeBinary(12), nullable=False)
    ciphertext = Column(LargeBinary, nullable=False)
    kms_key_id = Column(String(512), nullable=False)
    key_version = Column(Integer, nullable=False, default=1)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    refresh_expires_at = Column(DateTime(timezone=True), nullable=True)
    scopes = Column(JSONB, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class PublishJob(Base):
    __tablename__ = "native_publish_jobs"
    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    owner_id = Column(UUID(as_uuid=True), nullable=False, index=True)
    source_job_id = Column(String(128), nullable=True)
    media_reference = Column(Text, nullable=False)
    caption = Column(Text, nullable=False, default="")
    scheduled_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), nullable=False, default="pending")
    idempotency_key = Column(String(128), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_native_publish_owner_idempotency"),
    )


class PublishTarget(Base):
    __tablename__ = "native_publish_targets"
    id = Column(UUID(as_uuid=True), primary_key=True, default=_uuid)
    publish_job_id = Column(
        UUID(as_uuid=True), ForeignKey("native_publish_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    connection_id = Column(
        UUID(as_uuid=True), ForeignKey("native_social_connections.id", ondelete="RESTRICT"),
        nullable=False,
    )
    provider = Column(String(16), nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    provider_operation_id = Column(String(255), nullable=True)
    provider_post_id = Column(String(255), nullable=True)
    attempt_count = Column(Integer, nullable=False, default=0)
    error_code = Column(String(128), nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)
    __table_args__ = (
        UniqueConstraint("publish_job_id", "connection_id", name="uq_native_publish_job_connection"),
    )
