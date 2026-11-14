-- Additive PostgreSQL schema for the permission-aware clipping workflow.
-- This migration is not automatically applied by the existing OpenShorts stack.
BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS platform_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_subject TEXT NOT NULL UNIQUE,
    email TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS creators (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
    display_name TEXT NOT NULL,
    platform TEXT NOT NULL CHECK (platform IN ('youtube','instagram','tiktok','other')),
    platform_user_id TEXT,
    profile_url TEXT,
    contact_reference TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(owner_id, platform, platform_user_id)
);

CREATE TABLE IF NOT EXISTS source_videos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
    creator_id UUID REFERENCES creators(id) ON DELETE SET NULL,
    platform TEXT NOT NULL CHECK (platform IN ('youtube','instagram','tiktok','other','upload')),
    canonical_url TEXT NOT NULL,
    platform_video_id TEXT,
    title TEXT,
    language_code TEXT,
    published_at TIMESTAMPTZ,
    duration_seconds NUMERIC(10,3) CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_sha256 CHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(owner_id, canonical_url)
);

CREATE TABLE IF NOT EXISTS discovery_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
    source_video_id UUID NOT NULL REFERENCES source_videos(id) ON DELETE CASCADE,
    topic TEXT,
    language_code TEXT,
    views BIGINT CHECK (views IS NULL OR views >= 0),
    likes BIGINT CHECK (likes IS NULL OR likes >= 0),
    comments BIGINT CHECK (comments IS NULL OR comments >= 0),
    virality_score NUMERIC(7,4) CHECK (virality_score IS NULL OR virality_score BETWEEN 0 AND 100),
    score_version TEXT,
    discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(owner_id, source_video_id, discovered_at)
);

CREATE TABLE IF NOT EXISTS permission_requests (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
    source_video_id UUID NOT NULL REFERENCES source_videos(id) ON DELETE CASCADE,
    creator_id UUID REFERENCES creators(id) ON DELETE SET NULL,
    status TEXT NOT NULL DEFAULT 'UNREQUESTED'
        CHECK (status IN ('UNREQUESTED','DRAFT','REQUESTED','APPROVED','REJECTED','EXPIRED','REVOKED')),
    allowed_platforms TEXT[] NOT NULL DEFAULT '{}',
    commercial_use_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    editing_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    requested_at TIMESTAMPTZ,
    approved_at TIMESTAMPTZ,
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    revocation_reason TEXT,
    request_draft TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(owner_id, source_video_id),
    CHECK (expires_at IS NULL OR approved_at IS NULL OR expires_at > approved_at),
    CHECK (status <> 'APPROVED' OR approved_at IS NOT NULL),
    CHECK (status <> 'REVOKED' OR revoked_at IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS permission_evidence (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    permission_request_id UUID NOT NULL REFERENCES permission_requests(id) ON DELETE CASCADE,
    evidence_type TEXT NOT NULL CHECK (evidence_type IN ('email','message_export','document','url','manual_note')),
    evidence_reference TEXT NOT NULL,
    evidence_sha256 CHAR(64),
    recorded_by UUID NOT NULL REFERENCES platform_users(id),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS clipping_jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
    source_video_id UUID NOT NULL REFERENCES source_videos(id) ON DELETE RESTRICT,
    permission_request_id UUID NOT NULL REFERENCES permission_requests(id) ON DELETE RESTRICT,
    openshorts_job_id TEXT,
    status TEXT NOT NULL DEFAULT 'QUEUED'
        CHECK (status IN ('QUEUED','PROCESSING','COMPLETED','FAILED','CANCELLED')),
    config JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    UNIQUE(owner_id, openshorts_job_id)
);

CREATE TABLE IF NOT EXISTS generated_clips (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clipping_job_id UUID NOT NULL REFERENCES clipping_jobs(id) ON DELETE CASCADE,
    clip_index INTEGER NOT NULL CHECK (clip_index >= 0),
    file_reference TEXT NOT NULL,
    title TEXT,
    duration_seconds NUMERIC(10,3) CHECK (duration_seconds IS NULL OR duration_seconds > 0),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(clipping_job_id, clip_index),
    UNIQUE(file_reference)
);

CREATE TABLE IF NOT EXISTS publishing_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
    platform TEXT NOT NULL CHECK (platform IN ('youtube','instagram','tiktok')),
    provider_account_id TEXT NOT NULL,
    display_name TEXT,
    credential_reference TEXT NOT NULL,
    scopes TEXT[] NOT NULL DEFAULT '{}',
    connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    disconnected_at TIMESTAMPTZ,
    UNIQUE(owner_id, platform, provider_account_id)
);

CREATE TABLE IF NOT EXISTS publishing_posts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
    generated_clip_id UUID NOT NULL REFERENCES generated_clips(id) ON DELETE RESTRICT,
    caption TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS publishing_targets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    publishing_post_id UUID NOT NULL REFERENCES publishing_posts(id) ON DELETE CASCADE,
    publishing_account_id UUID NOT NULL REFERENCES publishing_accounts(id) ON DELETE RESTRICT,
    permission_request_id UUID NOT NULL REFERENCES permission_requests(id) ON DELETE RESTRICT,
    platform TEXT NOT NULL CHECK (platform IN ('youtube','instagram','tiktok')),
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT','SCHEDULED','PUBLISHING','PUBLISHED','FAILED','NEEDS_REVIEW','CANCELLED','BLOCKED')),
    scheduled_at TIMESTAMPTZ,
    idempotency_key TEXT NOT NULL UNIQUE,
    provider_post_id TEXT,
    provider_response JSONB,
    last_error TEXT,
    published_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(publishing_post_id, publishing_account_id)
);

CREATE TABLE IF NOT EXISTS analytics_snapshots (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    publishing_target_id UUID NOT NULL REFERENCES publishing_targets(id) ON DELETE CASCADE,
    captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    metrics JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE(publishing_target_id, captured_at)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id UUID NOT NULL REFERENCES platform_users(id) ON DELETE CASCADE,
    actor_id UUID REFERENCES platform_users(id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_id UUID,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_source_videos_owner_created ON source_videos(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_discovery_owner_score ON discovery_records(owner_id, virality_score DESC, discovered_at DESC);
CREATE INDEX IF NOT EXISTS idx_permissions_status_expiry ON permission_requests(owner_id, status, expires_at);
CREATE INDEX IF NOT EXISTS idx_clipping_jobs_status_created ON clipping_jobs(owner_id, status, created_at);
CREATE INDEX IF NOT EXISTS idx_publishing_targets_due ON publishing_targets(status, scheduled_at) WHERE status = 'SCHEDULED';
CREATE INDEX IF NOT EXISTS idx_publishing_targets_post ON publishing_targets(publishing_post_id);
CREATE INDEX IF NOT EXISTS idx_analytics_target_captured ON analytics_snapshots(publishing_target_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_owner_created ON audit_logs(owner_id, created_at DESC);

COMMIT;
