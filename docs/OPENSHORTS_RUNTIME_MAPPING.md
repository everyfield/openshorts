# OpenShorts Runtime Mapping

Inspection target: `everyfield/openshorts` at `f375e048fa2f6713506096d150bc76c67bad8b09` (2026-11-15).

This document records the current implementation before introducing replacement provider adapters. It does not claim that official Instagram, TikTok, or YouTube adapters exist.

## Verified current publishing path

- The FastAPI application is defined in root `app.py`.
- `POST /api/social/post` is the current publishing entry point.
- The handler validates that the requested job exists, ensures job files are available, checks job ownership, applies the existing social-post rate limit, resolves the Upload-Post API key/profile, then submits the request to Upload-Post.
- Upload-Post remains the only confirmed provider implementation on this path.
- `GET /api/social/post-status/{request_id}` polls Upload-Post's status endpoint and returns the overall status plus per-platform `success` and message fields.
- The application explicitly distinguishes Upload-Post request acceptance from final publication success. A successful HTTP submission is not proof that every target published.
- The API stores `request_id -> owner_id` in the process-local `_post_request_owners` dictionary. The source comment and implementation show that this mapping is in memory; it is not a durable publication ledger.
- The existing scheduled-post endpoints proxy Upload-Post's schedule API. The schedule listing filters returned rows by `profile_username` because the upstream schedule endpoint may return account-wide rows.
- The current profile and social-account connection flow is Upload-Post-specific. `resolve_upload_post` resolves the key and managed profile; `/api/social/user` and the profile/connect routes expose Upload-Post profiles and connected platform names.
- The current code includes a process-local rate-limit counter for managed publishing. It is not a durable, cross-process per-target scheduler.

## Existing account/token boundary

- The current social publishing integration uses an Upload-Post API key and Upload-Post profile identifier.
- This inspected path does not provide first-party Meta, TikTok, or YouTube OAuth token storage for the proposed native adapters.
- Do not assume the Upload-Post profile identifier is a provider OAuth account ID.
- Do not store provider refresh/access tokens in the browser, job JSON, or logs. A future native integration needs a dedicated server-side credential store, encryption/key-rotation strategy, owner-scoped account records, OAuth state/CSRF validation, refresh rotation, and disconnect/revocation handling.
- `yt_account.py` is unrelated to social publishing credentials: it coordinates YouTube extraction/download cookie-account pacing and rate limits. It must not be repurposed as OAuth credential storage.
- `yt_clients.py` configures yt-dlp extraction clients and PO-token provider arguments. It is not a YouTube Data API publishing adapter.

## Integration implications

1. Keep the Upload-Post path unchanged while native adapters are developed behind a separate interface and explicit feature flags.
2. Introduce durable publication records before enabling scheduled multi-platform publishing. A process-local request-owner map cannot safely support restart recovery, reliable deduplication, or durable per-platform outcomes.
3. Model each selected platform as an independent target with its own status, provider operation/post ID, timestamps, sanitized error code, and retry classification.
4. Persist a write-ahead publishing state before any non-idempotent provider write. If a request times out after it may have reached a provider, reconcile or mark it for manual review; do not blindly submit it again.
5. Keep provider status polling read-only. A poll must never initiate another upload.
6. Reuse the existing job ownership checks and ensure all new account, credential, target, and status queries are owner-scoped.
7. Preserve the existing Upload-Post schedule and analytics features until equivalent native behavior is implemented and tested.
8. Do not modify or import code from `cloud/`; its licensing is separate from the MIT-licensed core.
9. Do not add creator outreach, permission requests, approval workflows, evidence uploads, or permission/evidence storage. Those remain explicitly deferred.

## Not implemented / not verified

- Native Instagram OAuth, media-container publishing, and status reconciliation.
- Native TikTok OAuth, creator capability checks, chunked transfer, and Direct Post.
- Native YouTube Data API OAuth, resumable upload, and processing/status reconciliation.
- Durable native social-account credentials and refresh-token rotation.
- Durable per-platform publication ledger, idempotency constraints, scheduler/worker, and restart recovery.
- Dashboard account connection and per-target publication controls for native adapters.
- Mocked provider contract tests, integration tests, and live tests with provider-approved accounts.

No runtime code was changed as part of this mapping. The repository must be checked out and tested locally before runtime implementation can be considered complete.
