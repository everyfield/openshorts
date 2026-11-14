# Permission-aware clipping platform integration

## Repository inspection (2026-11-14)

### OpenShorts fork
- Repository: https://github.com/everyfield/openshorts
- Default branch: `main`
- Backend: Python 3.11, FastAPI, background threads/processes, FFmpeg, faster-whisper, Gemini-compatible LLM support, YOLO/MediaPipe.
- Frontend: React 18, Vite 4, Tailwind CSS.
- Existing publishing: Upload-Post integration, including TikTok draft mode configuration.
- Existing deployment: Docker Compose with backend, dashboard and Remotion renderer.
- Existing job persistence and runtime behavior must be inspected further before replacing any paths.
- Core license: MIT. The `cloud/` directory is explicitly excluded and uses `cloud/LICENSE` (OpenShorts Commercial License).

### Postline upstream
- Repository: https://github.com/adeel1608/social-media-scheduler
- Backend: Cloudflare Worker + Supabase Postgres/Auth + Cloudflare Queues/Cron.
- Frontend: React + Vite.
- Publishing adapters: official Meta Instagram, TikTok Content Posting, YouTube Data API and YouTube Analytics APIs.
- Scheduling: leased cron claims, queue dispatch and stable idempotency keys.
- Postline's README explicitly describes a single-owner self-hosted deployment, not a multi-tenant hosted SaaS.
- License: MIT (Copyright 2026 Adeel). No Postline source is copied in this integration commit; if code is imported later, retain its LICENSE and add the required attribution.

## Integration decisions

1. Keep OpenShorts' existing FastAPI + React/Vite + FFmpeg pipeline as the primary application.
2. Do not copy Postline's Cloudflare/Supabase deployment wholesale. Port only provider adapter behavior and scheduling concepts after mapping their interfaces and constraints.
3. Keep the current Upload-Post adapter operational. Add official provider adapters behind explicit feature/configuration gates; never claim an adapter is live-ready without approved credentials and a provider-confirmed test.
4. Do not implement creator outreach or automated permission requests in this release. Users manually record permission and attach evidence; no automated DM, comment, email, or outreach workflow.
5. Enforce permission checks in the backend immediately before creating or executing every publishing target. UI-only checks are insufficient.
6. Treat each platform target as an independent outcome. A failure on one target must not overwrite another target's confirmed success.
7. Preserve existing clip generation, transcription, tracking, crop, subtitle, rendering, API and MCP behavior.
8. Do not import or modify the `cloud/` subtree. It is not covered by the repository's MIT license.

## New database foundation

The first migration is additive and isolated under `database/migrations/`. It introduces creator, source, discovery, permission, clipping, account, publishing, analytics and audit records without changing existing OpenShorts job storage. The migration uses PostgreSQL and is intentionally not auto-applied by the current Docker Compose stack.

## Delivery sequence

- [x] Inspect the two upstream READMEs, licenses, runtime manifests and OpenShorts API entrypoint.
- [x] Confirm the OpenShorts fork and create an isolated feature branch.
- [x] Add an additive PostgreSQL schema for permission-aware workflow state.
- [x] Add a backend permission policy module and unit tests.
- [ ] Map the existing FastAPI job lifecycle and wire the policy into every publish entrypoint.
- [ ] Replace/extend Upload-Post with separately modeled official provider adapters.
- [ ] Add manual creator/source/permission management endpoints. No automated creator discovery or permission-request sending.
- [ ] Connect UI pages to real APIs and job states.
- [ ] Run migration, backend, frontend and end-to-end tests in a real checkout.
- [ ] Verify provider publication only with approved test accounts.

## Current limitations

This commit is a safe foundation, not a completed platform. The schema is not yet wired to the running application, the policy module is not yet called by existing publish routes, and no provider credentials or live social accounts have been tested. Automated creator outreach/permission requests are explicitly out of scope; permission must be entered and evidenced manually. The current OpenShorts application must not be described as having permission enforcement until those wiring and tests are complete.


## Existing publish-path audit (2026-11-15)

See [PUBLISH_PATH_AUDIT.md](./PUBLISH_PATH_AUDIT.md). The confirmed direct entry point is `POST /api/social/post` in `app.py`; it submits clips to Upload-Post and currently has no creator-permission check. Scheduled posts use the same handler. Completed-job autopilot delegates to `cloud.autopilot.on_job_finished` and requires a separate audit. The new policy/schema are not wired into either path.
