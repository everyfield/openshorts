# Clipping and social publishing integration

## Repository inspection (2026-11-14 to 2026-11-15)

### OpenShorts fork
- Repository: https://github.com/everyfield/openshorts
- Default branch: `main`
- Backend: Python 3.11, FastAPI, background threads/processes, FFmpeg, faster-whisper, Gemini-compatible LLM support, YOLO/MediaPipe.
- Frontend: React 18, Vite 4, Tailwind CSS.
- Existing publishing: Upload-Post integration, including TikTok draft mode configuration.
- Existing deployment: Docker Compose with backend, dashboard and Remotion renderer.
- Core license: MIT. The `cloud/` directory is explicitly excluded and uses `cloud/LICENSE` (OpenShorts Commercial License).

### Postline upstream
- Repository: https://github.com/adeel1608/social-media-scheduler
- Backend: Cloudflare Worker + Supabase Postgres/Auth + Cloudflare Queues/Cron.
- Frontend: React + Vite.
- Publishing adapters: official Meta Instagram, TikTok Content Posting, YouTube Data API and YouTube Analytics APIs.
- Scheduling: leased cron claims, queue dispatch and stable idempotency keys.
- Postline's README describes a single-owner self-hosted deployment, not a multi-tenant hosted SaaS.
- License: MIT (Copyright 2026 Adeel). No Postline source has been copied in this integration. If code is imported later, retain its LICENSE and required attribution.

## Integration decisions

1. Keep OpenShorts' existing FastAPI + React/Vite + FFmpeg pipeline as the primary application.
2. Do not copy Postline's Cloudflare/Supabase deployment wholesale. Port only useful provider adapter and scheduling concepts after mapping their interfaces and constraints.
3. Keep the current Upload-Post adapter operational. Add official provider adapters behind explicit feature/configuration gates; never claim an adapter is live-ready without approved credentials and a provider-confirmed test.
4. Do not build creator outreach, permission registration, permission approval workflows, permission evidence uploads, or permission/evidence storage in the current scope.
5. Preserve existing clip generation, transcription, tracking, crop, subtitle, rendering, API and MCP behavior.
6. Treat each platform target as an independent outcome. A failure on one target must not overwrite another target's confirmed success.
7. Do not import or modify the `cloud/` subtree. It is not covered by the repository's MIT license.

## Verified existing publishing path

See [OPENSHORTS_RUNTIME_MAPPING.md](./OPENSHORTS_RUNTIME_MAPPING.md).

- FastAPI `POST /api/social/post` currently submits to Upload-Post.
- `GET /api/social/post-status/{request_id}` polls Upload-Post and returns per-platform results.
- The `request_id -> owner_id` association is process-local memory, not a durable publication ledger.
- Current social account connection/profile discovery is Upload-Post-specific.
- `yt_account.py` and `yt_clients.py` are for yt-dlp extraction/download, not social publishing OAuth or Data API uploads.

## Persistence and dashboard findings

See [NATIVE_PUBLISHING_ARCHITECTURE.md](./NATIVE_PUBLISHING_ARCHITECTURE.md).

- The inspected core tree has no independent core social-account/publishing persistence model or migration set.
- Root `alembic/env.py` imports `cloud.database.Base` and `cloud.models`; using it for new core schema would couple the MIT core to separately licensed code.
- `AccountPage.jsx` is account/billing UI; `AutopilotTab.jsx` is coupled to cloud Autopilot and Upload-Post. Neither is a neutral native provider connection manager.
- Existing social profile tests exercise Upload-Post/cloud behavior, not native OAuth or durable native publishing.
- A core-compatible database/migration boundary must be established before creating native account/credential/publication tables. Do not assume the open-source core has the cloud PostgreSQL schema.
- Native provider connections and publishing UI must be separate from existing Upload-Post/Autopilot until native parity is verified.

## Delivery sequence

- [x] Inspect upstream READMEs, licenses, runtime manifests and OpenShorts API entrypoint.
- [x] Confirm the OpenShorts fork and create an isolated feature branch.
- [x] Document integration architecture and licensing constraints.
- [x] Research Postline provider adapter contracts and Python porting requirements.
- [x] Map existing Upload-Post publishing/status path and account credential boundary.
- [x] Inspect current dashboard social account/Autopilot entry points and existing social regression tests.
- [x] Identify the persistence/migration licensing boundary and document the native publishing data model.
- [x] Implement isolated native-social SQLAlchemy metadata/bootstrap and owner-scoped connection/credential repository without importing `cloud/`.
- [x] Add KMS-backed AES-256-GCM envelope encryption with tenant/provider-bound authenticated context and no plaintext fallback.
- [x] Add a versioned native-social migration runner with a PostgreSQL advisory lock; PostgreSQL integration tests remain pending.
- [x] Add a provider-neutral Python protocol, typed request/result models, and state-transition/retry unit tests; provider HTTP mock tests remain pending.
- [ ] Implement and test YouTube native publishing.
- [x] Implement Instagram Login OAuth start/callback with DB-backed hashed, expiring, single-use state and authenticated tenant ownership.\n- [x] Persist validated professional-account tokens through KMS envelope encryption.\n- [ ] Implement and test end-to-end Instagram Reels publishing.
- [ ] Implement and test TikTok Content Posting API, keeping public Direct Post disabled until audit requirements are met.
- [ ] Add durable scheduling, idempotency, reconciliation, and restart recovery.
- [ ] Connect native account workflows and per-target publication states to a separate dashboard section.
- [ ] Run backend, frontend and end-to-end tests in a real checkout.
- [ ] Verify provider publication only with provider-approved test accounts.

## Current limitations

This branch now includes provider-neutral runtime contracts, state-transition/retry helpers, isolated native-social tables, owner-scoped credential persistence, and KMS-backed credential encryption. Instagram OAuth routes, token persistence wiring, durable scheduling/worker recovery, and native dashboard workflows remain unimplemented. YouTube and TikTok are explicitly deferred. Tests have not been executed in a real checkout; no PostgreSQL integration, backend/frontend suite, or live provider test has been run. Permission management and evidence storage are intentionally deferred.


## Native persistence implementation update (2026-11-15)

Implemented on the feature branch:
- Independent SQLAlchemy metadata and tables in `native_social/models.py`; no imports from `cloud.models` and no changes to `cloud/`.
- Hosted-mode startup initializes only the native-social tables against the configured hosted database URL; shutdown disposes the separate connection pool.
- Owner-scoped connection/credential repository functions are in `native_social/persistence.py`.
- OAuth token payloads use envelope encryption: AES-256-GCM data encryption keys are generated by AWS KMS. KMS encryption context binds ciphertext to owner, connection, provider and purpose.
- Encryption fails closed unless `NATIVE_SOCIAL_KMS_KEY_ID` is configured and KMS is reachable. No plaintext fallback or local key fallback exists.
- Four KMS encryption tests were added, including owner/context binding and missing-key failure.

Not implemented yet:
- OAuth start/callback routes, provider API clients, token refresh/revocation workflows and dashboard connection UI.
- Repository/database integration tests against PostgreSQL.
- Tests have not been executed in a real checkout. The schema currently uses isolated SQLAlchemy `create_all` for new tables; it does not yet have a versioned native-social migration runner. Existing tables are not altered by `create_all`.
- Hosted deployment must configure the KMS key and grant the application role `kms:GenerateDataKey` and `kms:Decrypt` on that key before OAuth credentials can be stored or read.


## YouTube API transport implementation update (2026-11-15)

YouTube provider work is deferred and is not part of the active implementation scope. Earlier YouTube transport code was removed from this branch.
- Server-side refresh-token exchange at Google's OAuth token endpoint.
- Authenticated `channels.list(mine=true)` account validation.
- `videos.insert` resumable upload using 8 MiB chunks and upload-session status reconciliation after transport/5xx failures.
- Read-only `videos.list` processing/publication status lookup.
- Google OAuth token revocation.
- Private visibility is the default. Public and unlisted uploads are blocked unless the deployment explicitly enables the post-audit gate.
- Upload session URLs are accepted only on HTTPS `www.googleapis.com`; they are not logged.
- Mock HTTP tests cover refresh, account validation, resumable chunk transfer, private-only gate, and status mapping.

The transport client is not yet wired to OAuth start/callback routes, the encrypted credential repository, or the existing clip-job media resolver. Do not treat YouTube publishing as end-to-end available yet. The tests have not been run in a real checkout.


## Instagram-only implementation update (2026-11-15)

The current provider implementation scope is Instagram only. YouTube and TikTok are not part of the active delivery scope.

Added `native_social/instagram.py`:
- Instagram Login OAuth authorization URL with least-privilege scopes: `instagram_business_basic` and `instagram_business_content_publish`.
- Authorization-code exchange, long-lived token exchange, and long-lived token refresh.
- Professional account identity validation.
- Reels container creation, processing-status polling, and the separate publish call.
- Public HTTPS media URL validation; localhost and private/reserved IP literals are rejected.
- Mock tests for OAuth, account validation, Reels container creation/status, publish, URL validation, and token-safe error messages.

This is an API transport foundation only. Authenticated OAuth start/callback, DB-backed single-use state validation, professional-account verification, and KMS-encrypted token persistence are now implemented. Remaining work: connect generated clips to stable public HTTPS media storage, durable create/poll/publish orchestration with duplicate-publish reconciliation, token refresh lifecycle, PostgreSQL integration tests, and a provider-approved live test. The routes are registered only in hosted billing mode. The callback redirects to the application root with a fixed status query; frontend status handling is not yet implemented. Instagram supports professional Business/Creator accounts, not personal consumer accounts. Meta's official collection documents the Instagram Login permissions and the container → status → publish workflow.


### Instagram OAuth route implementation update (2026-11-15)

Implemented on the feature branch:
- Hosted-only FastAPI routes: `GET /api/native-social/instagram/connect` and `GET /api/native-social/instagram/callback`.
- Connect requires the existing SaaS authenticated-user dependency; owner IDs are never accepted from client input.
- OAuth state is generated with a cryptographically secure random generator, stored only as a SHA-256 digest, expires after 10 minutes, and is consumed atomically once in PostgreSQL. Expired/old consumed rows are pruned when a new connection starts.
- Callback exchanges the authorization code, obtains a long-lived token, validates the professional account, and stores credentials through the KMS-backed repository.
- Callback failures return a fixed relative dashboard redirect and do not expose provider errors, authorization codes, state values, or tokens.
- Added focused tests for state hashing and fixed redirect behavior. These tests have not been executed in a real checkout.

Remaining: stable public HTTPS media delivery, durable Reel container/publish worker with duplicate-publish reconciliation, refresh/disconnect lifecycle, dashboard integration, PostgreSQL integration tests, and Meta-approved live account validation/publication. Set `INSTAGRAM_REDIRECT_URI` to the exact callback URL `https://<your-api-host>/api/native-social/instagram/callback`. Hosted runtime also requires `INSTAGRAM_APP_ID`, `INSTAGRAM_APP_SECRET`, `NATIVE_SOCIAL_KMS_KEY_ID`, and the app role's KMS permissions.


### Instagram account dashboard update (2026-11-15)

- Added an authenticated settings card for native Instagram Business/Creator accounts.
- The dashboard requests the OAuth URL through the authenticated API client (so the stored bearer session is sent); it does not navigate directly to an endpoint that would lose the Authorization header.
- Added owner-scoped connection listing and disconnect endpoints. Disconnect deletes the connection row and its encrypted credential through the database foreign-key cascade; the user is informed that app access can also be revoked in Instagram settings.
- OAuth callback now returns to the configured `FRONTEND_URL` Settings tab and reports a non-sensitive success/error status. The connect endpoint returns an authorization URL as JSON so the dashboard can attach its bearer session before redirecting to Meta.
- The account card is explicitly labeled as native publishing in progress. It does not claim Reels publishing is already wired to generated clips.
- Frontend build, browser OAuth round-trip, PostgreSQL cascade behavior, and live Instagram tests remain unexecuted.
