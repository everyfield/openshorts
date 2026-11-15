# OpenShorts Instagram publishing integration

Updated: 2026-11-15

## Scope and constraints

- Active native publishing platform: Instagram Reels only.
- Preserve the existing OpenShorts clipping pipeline, Upload-Post integration, and Autopilot behavior.
- Do not modify or copy `cloud/`; it is separately licensed. The integration calls existing hosted auth, database, and storage interfaces.
- Do not implement YouTube, TikTok, creator outreach, automated permission requests, permission approval, or evidence storage.
- Instagram Login supports professional Business and Creator accounts; personal consumer accounts are not supported.

## Implemented

### Account connection
- Hosted-only OAuth start and callback:
  - `GET /api/native-social/instagram/connect`
  - `GET /api/native-social/instagram/callback`
- Connect requires the authenticated SaaS user. Owner ID is never accepted from client input.
- OAuth state is cryptographically random, stored as a SHA-256 digest, expires after 10 minutes, and is consumed atomically once in PostgreSQL.
- Callback exchanges the authorization code, obtains a long-lived token, validates the professional account, and persists credentials through AWS KMS envelope encryption.
- Owner-scoped list and disconnect routes; disconnect deletes the local encrypted credential and marks the connection disconnected while preserving rows referenced by publish history. Reconnecting the same Instagram account reuses its connection record.
- Settings UI supports connect, list, disconnect, and clear health states (`connected`, `refresh_degraded`, `reauth_required`). Reconnect uses the existing OAuth flow.

### Provider API
- Instagram Login authorization, code exchange, long-lived token exchange, and refresh transport. Long-lived tokens are refreshed automatically when they enter the 14-day expiry window; refresh is attempted at most once per six hours after a failure.
- Professional-account identity validation.
- Reels container creation, exact processing status lookup, and explicit publish request.
- HTTPS media URL validation; localhost and private/reserved IP literals are rejected.

### Durable publish records and API
- Independent SQLAlchemy metadata and versioned native-social migrations; no dependency on `cloud.models`.
- `native_publish_jobs` and `native_publish_targets` persist request and per-account outcome.
- `GET /api/native-social/instagram/clips` lists the authenticated user's archived R2 clips. It returns clip IDs and metadata, never signed media URLs.
- `POST /api/native-social/instagram/publish` accepts `connection_id`, owner-owned `clip_id`, `caption` (max 2,200 characters), and `idempotency_key` (1–128 characters).
- The server verifies clip ownership against the existing hosted video library and stores only the owner-scoped R2 object key. The worker generates a 24-hour R2 presigned URL immediately before Meta fetches the clip. The signed URL is never stored in the database or sent to the dashboard.
- `GET /api/native-social/instagram/publishes/{job_id}` returns owner-scoped status.
- `GET /api/native-social/instagram/publishes?limit=50` returns the signed-in owner’s latest publish history (maximum 100), including account, caption, status, media ID, timestamps, and safe error codes.
- `POST /api/native-social/instagram/publishes/{job_id}/reconcile` lets the owner manually record `published` (optional media ID) or `not_published` after checking Instagram. It only accepts `needs_review` jobs and never calls Meta or retries a publish.
- Publish requests are persisted and return immediately. A hosted-lifespan worker polls the PostgreSQL queue.
- The worker persists the container ID, polls until provider status is `FINISHED`, commits a `publishing` claim before calling `media_publish`, and persists the returned media ID.
- Active jobs hold a PostgreSQL worker lease renewed by a 20-second heartbeat (90-second lease). The worker checks for expired leases every 60 seconds and recovers only expired work, so long-running active jobs are not requeued just because they exceeded a fixed age.
- Expired `processing` jobs resume polling only when the Instagram container ID was persisted. If a worker stops after container creation may have reached Meta but before the ID is saved, the job moves to `needs_review` rather than blindly creating another container.
- Jobs interrupted in `publishing` move to `needs_review` and are never automatically resubmitted. Worker-token checks fence stale workers from overwriting a newer worker's state.
- Same-key duplicate requests return the existing job; reusing a key with different request data is rejected.
- Ambiguous transport or publish outcomes are marked `needs_review`; non-idempotent publishing is not blindly retried.

### Dashboard
- Connect/disconnect professional Instagram accounts.
- Load archived clips from the signed-in user's R2 library.
- Select the Instagram account and clip, edit the caption, submit a Reel, and poll status for up to 135 seconds.
- Browse the latest 50 publish attempts, inspect status/media ID/error code, and manually reconcile ambiguous `needs_review` jobs. Disconnecting an account removes its encrypted token but does not delete its historical publish records. A confirmed `not_published` result is terminal; a new publish request must be created explicitly to avoid accidental duplicate submission.
- Existing Upload-Post and Autopilot remain separate.

### Credential security
- OAuth credentials use AES-256-GCM envelope encryption with AWS KMS-generated data keys.
- KMS encryption context binds ciphertext to owner, connection, provider, and purpose.
- No plaintext or local-key fallback. Set `NATIVE_SOCIAL_KMS_KEY_ID`; grant the application role `kms:GenerateDataKey` and `kms:Decrypt`.
- Refresh claims use a PostgreSQL lease to avoid concurrent refreshes across app replicas. Refreshed credentials are re-encrypted with a new KMS data key; raw tokens and provider error messages are never written to logs or UI.
- Expired or authentication-rejected tokens move to `reauth_required`; transient refresh failures move to `refresh_degraded` and retain publish access while retrying.

## Remaining before production

1. Add PostgreSQL repository/migration integration tests and execute backend/frontend test suites.
2. Complete OAuth and Reels publishing tests with a Meta-approved professional test account.
3. Verify multi-replica worker recovery and lease fencing under process termination and network timeouts.

## Runtime prerequisites

- `INSTAGRAM_APP_ID`
- `INSTAGRAM_APP_SECRET`
- `INSTAGRAM_REDIRECT_URI=https://<api-host>/api/native-social/instagram/callback`
- `NATIVE_SOCIAL_KMS_KEY_ID` and the KMS IAM permissions above
- A Meta app configured for Instagram Login and the required permissions
- Hosted R2 storage configured; clips appear in the picker after they have been archived to the user's library

Optional bounded processing controls:
- `INSTAGRAM_PROCESSING_MAX_POLLS`: default 24, maximum 60
- `INSTAGRAM_PROCESSING_POLL_SECONDS`: default 5 seconds, maximum 30

## Verification status

Mock/unit tests have been added for OAuth, provider transport, credential encryption, and state transitions. They have **not** been executed in a real checkout in this work session. No PostgreSQL integration test, backend test suite, frontend build, browser OAuth round-trip, or live Instagram publication has been verified. Do not treat this branch as production-ready or claim live publishing has been confirmed.
