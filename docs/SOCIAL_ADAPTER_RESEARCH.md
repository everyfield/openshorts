# Social Publishing Adapter Research

Research date: 2026-11-15

## Decision

Do not copy Postline's application or worker wholesale. OpenShorts is a Python/FastAPI + React/Vite + FFmpeg application, while Postline is a TypeScript/Cloudflare Worker + Supabase application. Port only provider-independent contracts and the provider-specific HTTP behavior that can be cleanly isolated behind the existing OpenShorts backend.

Postline's adapter package is MIT-licensed. If any source is copied, preserve its LICENSE and copyright attribution in this repository. No Postline implementation source is copied by this document.

## Existing OpenShorts integration

The current OpenShorts publishing integration uses Upload-Post. The direct publishing route is `POST /api/social/post`; scheduled requests use that same route. The existing integration should remain available until each replacement adapter has equivalent account setup, media transfer, status handling, and tests.

Do not modify the `cloud/` subtree. It has a separate commercial license.

## Postline implementation references inspected

- Adapter contract and implementations: https://github.com/adeel1608/social-media-scheduler/tree/main/packages/platforms/src
- Shared adapter types: https://github.com/adeel1608/social-media-scheduler/blob/main/packages/platforms/src/types.ts
- Instagram adapter: https://github.com/adeel1608/social-media-scheduler/blob/main/packages/platforms/src/instagram.ts
- TikTok adapter: https://github.com/adeel1608/social-media-scheduler/blob/main/packages/platforms/src/tiktok.ts
- YouTube adapter: https://github.com/adeel1608/social-media-scheduler/blob/main/packages/platforms/src/youtube.ts
- HTTP safety helpers: https://github.com/adeel1608/social-media-scheduler/blob/main/packages/platforms/src/http.ts
- Adapter tests: https://github.com/adeel1608/social-media-scheduler/blob/main/packages/platforms/test/adapters.test.ts

Postline's architecture uses independent per-platform targets, idempotency keys, a durable publication state machine, write-ahead markers before non-idempotent provider writes, read-only status polling, and a `needs_review` state for ambiguous results. These are useful design patterns to reproduce in Python rather than importing the Cloudflare/Supabase runtime.

## Provider constraints that must remain explicit

### Instagram

- Use the official Instagram Platform publishing API and OAuth flow.
- The account must be eligible (professional account: Business or Creator).
- Model container creation, processing status, and final publish as distinct steps.
- Store provider media/container IDs and report final provider status; an accepted request is not proof that a post is published.
- Request only the OAuth scopes needed by the enabled features.
- Insights must preserve unavailable metrics as unavailable; never substitute zero for missing values.

Official setup reference: https://developers.facebook.com/docs/instagram-platform/

### TikTok

- Use the official Content Posting API; no browser automation, scraping, password collection, or unofficial endpoints.
- Query creator information and use the privacy options returned by the API.
- Unaudited Direct Post clients are restricted to private visibility. Public posting must remain disabled until the required TikTok audit is confirmed.
- Do not silently rewrite a requested public post to private.
- Respect chunk upload rules, creator posting limits, and provider rate limits.
- Distinguish accepted/init, transfer, processing, and final publication status.

Postline's implementation notes and official references: https://github.com/adeel1608/social-media-scheduler/blob/main/docs/TIKTOK_SETUP.md

### YouTube

- Use the official YouTube Data API upload flow and OAuth scopes.
- Treat upload processing and final video status separately.
- Public visibility may require YouTube API audit approval; do not claim production public posting before the required approval.
- Persist the returned video ID and reconcile status before marking a target successful.

Postline adapter reference: https://github.com/adeel1608/social-media-scheduler/blob/main/packages/platforms/src/youtube.ts

## Proposed Python adapter boundary

Each adapter should implement the same backend-owned interface:

- `validate_account()`: verify token/account status and required scopes.
- `publish_media()`: initiate and perform provider writes; return a provider operation identifier.
- `get_publish_status()`: read-only status lookup; must never issue another publish write.
- `refresh_credentials()`: refresh OAuth credentials with rotation-safe persistence.
- `revoke_credentials()`: disconnect/revoke through the provider's documented endpoint.

Common request/result fields:
- platform
- account_id
- media_url or streamed media source
- caption/title/description
- scheduled_at (UTC)
- provider_operation_id
- provider_post_id
- status
- error_code (stable, sanitized)
- retry_class (safe, definite_failure, ambiguous)

Provider access tokens and refresh tokens must remain server-side. Never place credentials or signed media URLs in browser state, job queue payloads, logs, or error messages.

## Publishing state machine

Recommended per-platform target states:

`draft -> scheduled -> queued -> publishing -> processing -> published`

Terminal/recovery states:
- `failed`: provider definitively rejected the operation; user may explicitly retry.
- `needs_review`: request may have reached the provider but the result is ambiguous; do not automatically submit again.
- `cancelled`: cancelled before a provider write begins.

Do not automatically retry a non-idempotent provider publish request after a timeout. Persist a write-ahead marker before the provider request and reconcile through a provider operation ID/status endpoint whenever available. Each platform target must transition independently.

## Implementation order

1. Map current OpenShorts account/token storage and publishing request/response models.
2. Introduce a Python adapter protocol and a mock transport test harness without changing current Upload-Post behavior.
3. Implement YouTube first, because its upload/status contract is comparatively straightforward and can be isolated.
4. Implement Instagram container/publish lifecycle and account capability checks.
5. Implement TikTok creator-info/privacy/chunk-upload/status flow, with public posting disabled until audit approval is configured.
6. Add OAuth connection flows, encrypted token storage, refresh rotation, and disconnect behavior.
7. Add independent scheduled targets and durable idempotency/state transitions.
8. Connect the dashboard to account connection, platform selection, scheduling, and per-target status.
9. Run mocked provider tests, backend/frontend tests, and controlled live tests with explicitly authorized test accounts.

## Not implemented yet

This document is research and architecture only. It does not add provider adapters, OAuth routes, credential storage, database migrations, dashboard screens, or publishing behavior. No live provider publication has been tested.
