# OpenShorts Instagram publishing integration

Updated: 2026-11-15

## Scope and constraints

- Active native publishing platform: Instagram Reels only.
- Preserve the existing OpenShorts clipping pipeline, Upload-Post integration, and Autopilot behavior.
- Do not modify or copy `cloud/`; it is separately licensed.
- Do not implement YouTube, TikTok, creator outreach, automated permission requests, permission approval, or evidence storage.
- Instagram Login supports professional Business and Creator accounts; personal consumer accounts are not supported by this integration.

## Implemented

### Account connection
- Hosted-only OAuth start and callback routes:
  - `GET /api/native-social/instagram/connect`
  - `GET /api/native-social/instagram/callback`
- Connect requires the authenticated SaaS user. The owner ID is never accepted from client input.
- OAuth state is cryptographically random, stored as a SHA-256 digest, expires after 10 minutes, and is consumed atomically once in PostgreSQL.
- Callback exchanges the authorization code, obtains a long-lived token, validates the professional account, and persists credentials through AWS KMS envelope encryption.
- Owner-scoped list and disconnect routes; disconnect deletes the local encrypted credential.
- Settings UI supports connect, list, and disconnect.

### Provider API
- Instagram Login authorization, code exchange, long-lived token exchange, and refresh transport.
- Professional-account identity validation.
- Reels container creation, exact processing status lookup, and explicit publish request.
- Public HTTPS media URL validation; localhost and private/reserved IP literals are rejected.

### Durable publish records and API
- Independent SQLAlchemy metadata and versioned native-social migrations; no dependency on `cloud.models`.
- `native_publish_jobs` and `native_publish_targets` persist request and per-account outcome.
- `GET /api/native-social/instagram/clips` lists the authenticated user's archived R2 clips. It returns clip IDs and metadata, never signed media URLs.
- `POST /api/native-social/instagram/publish` accepts `connection_id`, owner-owned `clip_id`, `caption` (max 2,200 characters), and `idempotency_key` (1–128 characters).
- The server verifies clip ownership against the existing hosted video library and stores only the owner-scoped R2 object key in the publish job. The worker generates a 24-hour R2 presigned URL immediately before Meta fetches the clip. The signed URL is never stored in the database or sent to the dashboard.
- `GET /api/native-social/instagram/publishes/{job_id}` returns owner-scoped status.
- Publish requests are persisted and return immediately. A hosted-lifespan worker polls the PostgreSQL queue and advances each job.
- The worker persists the container ID, polls until provider status is `FINISHED`, commits a `publishing` claim before calling `media_publish`, and persists the returned media ID.
- On startup, jobs left in `processing` for at least 15 minutes are requeued; jobs interrupted in `publishing` are moved to `needs_review` and are never automatically resubmitted.
- Same-key duplicate requests return the existing job; reusing a key with different request data is rejected.
- Ambiguous transport or publish outcomes are marked `needs_review`; non-idempotent publishing is not blindly retried.

### Credential security
- OAuth credentials use AES-256-GCM envelope encryption with AWS KMS-generated data keys.
- KMS encryption context binds ciphertext to owner, connection, provider, and purpose.
- No plaintext or local-key fallback. Set `NATIVE_SOCIAL_KMS_KEY_ID`; grant the application role `kms:GenerateDataKey` and `kms:Decrypt`.

## Not complete

1. Connect the actual generated clip artifact to the publish endpoint. The current API requires a public HTTPS media URL; the dashboard does not yet resolve a selected clip into a publish request.
2. Move the orchestration out of the HTTP request into a durable background worker/queue with leases, retry policy, and restart recovery. Current status and phase records are durable, but long provider processing may exceed an HTTP timeout.
3. Implement safe recovery for stale `processing` jobs and reconciliation of ambiguous container creation/publish outcomes.
4. Add token refresh scheduling and mark expired/revoked connections as requiring re-authentication.
5. Add dashboard publish submission, per-job status, and error/reconciliation UI.
6. Add PostgreSQL repository/migration integration tests and execute backend/frontend test suites.
7. Complete OAuth and Reels publishing tests with a Meta-approved professional test account.

## Runtime prerequisites

- `INSTAGRAM_APP_ID`
- `INSTAGRAM_APP_SECRET`
- `INSTAGRAM_REDIRECT_URI=https://<api-host>/api/native-social/instagram/callback`
- `NATIVE_SOCIAL_KMS_KEY_ID` and the KMS IAM permissions above
- A Meta app configured for Instagram Login and the required permissions
- Publicly fetchable HTTPS video URLs supported by Meta's Reels publishing requirements

Optional bounded processing controls:
- `INSTAGRAM_PROCESSING_MAX_POLLS`: default 24, maximum 60
- `INSTAGRAM_PROCESSING_POLL_SECONDS`: default 5 seconds, maximum 30

## Verification status

Mock/unit tests have been added for OAuth, provider transport, credential encryption, and state transitions. They have **not** been executed in a real checkout in this work session. No PostgreSQL integration test, frontend build, browser OAuth round-trip, or live Instagram publication has been verified. Do not treat this branch as production-ready or claim live publishing has been confirmed.
