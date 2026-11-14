-- Enforce permission state transitions and publish authorization in PostgreSQL.
-- Apply only after 0001_permission_aware_workflow.sql.
BEGIN;

CREATE OR REPLACE FUNCTION enforce_permission_state_transition()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status = OLD.status THEN RETURN NEW; END IF;
    IF NOT (
        (OLD.status = 'UNREQUESTED' AND NEW.status = 'DRAFT') OR
        (OLD.status = 'DRAFT' AND NEW.status = 'REQUESTED') OR
        (OLD.status = 'REQUESTED' AND NEW.status IN ('APPROVED', 'REJECTED')) OR
        (OLD.status = 'APPROVED' AND NEW.status IN ('REVOKED', 'EXPIRED'))
    ) THEN
        RAISE EXCEPTION 'invalid permission transition: % -> %', OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'APPROVED' AND NEW.approved_at IS NULL THEN
        RAISE EXCEPTION 'approval timestamp is required' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'REVOKED' AND NEW.revoked_at IS NULL THEN
        RAISE EXCEPTION 'revocation timestamp is required' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_permission_state_transition ON permission_requests;
CREATE TRIGGER trg_permission_state_transition
BEFORE UPDATE OF status ON permission_requests
FOR EACH ROW EXECUTE FUNCTION enforce_permission_state_transition();

CREATE OR REPLACE FUNCTION enforce_publish_target_permission()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    permission_row permission_requests%ROWTYPE;
    target_owner UUID;
    clip_source UUID;
    account_platform TEXT;
BEGIN
    IF NEW.status NOT IN ('SCHEDULED', 'PUBLISHING', 'PUBLISHED') THEN RETURN NEW; END IF;

    SELECT pp.owner_id, cj.source_video_id
      INTO target_owner, clip_source
      FROM publishing_posts pp
      JOIN generated_clips gc ON gc.id = pp.generated_clip_id
      JOIN clipping_jobs cj ON cj.id = gc.clipping_job_id
     WHERE pp.id = NEW.publishing_post_id;
    IF target_owner IS NULL OR clip_source IS NULL THEN
        RAISE EXCEPTION 'publishing post or source clip not found' USING ERRCODE = 'foreign_key_violation';
    END IF;

    SELECT pa.platform INTO account_platform
      FROM publishing_accounts pa
     WHERE pa.id = NEW.publishing_account_id
       AND pa.owner_id = target_owner
       AND pa.disconnected_at IS NULL;
    IF account_platform IS NULL OR account_platform <> NEW.platform THEN
        RAISE EXCEPTION 'publishing account does not match target owner/platform'
            USING ERRCODE = 'check_violation';
    END IF;

    SELECT * INTO permission_row
      FROM permission_requests pr
     WHERE pr.id = NEW.permission_request_id
       AND pr.owner_id = target_owner
       AND pr.source_video_id = clip_source
     FOR SHARE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'matching permission record not found' USING ERRCODE = 'check_violation';
    END IF;

    IF permission_row.status <> 'APPROVED'
       OR permission_row.approved_at IS NULL
       OR permission_row.approved_at > now()
       OR permission_row.revoked_at IS NOT NULL
       OR (permission_row.expires_at IS NOT NULL AND permission_row.expires_at <= now())
       OR NOT (NEW.platform = ANY(permission_row.allowed_platforms)) THEN
        RAISE EXCEPTION 'publishing blocked: permission missing, invalid, expired, revoked or out of scope'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_publish_target_permission ON publishing_targets;
CREATE TRIGGER trg_publish_target_permission
BEFORE INSERT OR UPDATE OF status, permission_request_id, publishing_account_id, platform, publishing_post_id
ON publishing_targets
FOR EACH ROW EXECUTE FUNCTION enforce_publish_target_permission();

CREATE OR REPLACE FUNCTION block_targets_after_permission_change()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status IN ('REVOKED', 'EXPIRED') AND OLD.status = 'APPROVED' THEN
        UPDATE publishing_targets pt
           SET status = 'BLOCKED',
               last_error = 'Permission revoked or expired; manual review required',
               updated_at = now()
          FROM publishing_posts pp
          JOIN generated_clips gc ON gc.id = pp.generated_clip_id
          JOIN clipping_jobs cj ON cj.id = gc.clipping_job_id
         WHERE pt.publishing_post_id = pp.id
           AND pt.permission_request_id = NEW.id
           AND cj.source_video_id = NEW.source_video_id
           AND pt.status IN ('DRAFT', 'SCHEDULED', 'NEEDS_REVIEW');
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_block_targets_after_permission_change ON permission_requests;
CREATE TRIGGER trg_block_targets_after_permission_change
AFTER UPDATE OF status ON permission_requests
FOR EACH ROW EXECUTE FUNCTION block_targets_after_permission_change();

COMMIT;
