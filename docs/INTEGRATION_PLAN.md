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

See [OPENSHORTS_RUNTIME_MAPPING.md](./OPENSHORTS_RUNTIME_MAPPING.md) for the inspected implementation details.

- FastAPI `POST /api/social/post` currently submits to Upload-Post.
- `GET /api/social/post-status/{request_id}` polls Upload-Post and returns per-platform results.
- The `request_id -> owner_id` association is process-local memory, not a durable publication ledger.
- Current social account connection/profile discovery is Upload-Post-specific.
- `yt_account.py` and `yt_clients.py` are for yt-dlp extraction/download, not social publishing OAuth or Data API uploads.

## Delivery sequence

- [x] Inspect the two upstream READMEs, licenses, runtime manifests and OpenShorts API entrypoint.
- [x] Confirm the OpenShorts fork and create an isolated feature branch.
- [x] Document the integration architecture and licensing constraints.
- [x] Research Postline's official provider adapter contracts and document Python porting requirements.
- [x] Map the existing Upload-Post publishing/status path and account credential boundary.
- [ ] Inspect the job persistence model and dashboard social-account/publishing UI in detail.
- [ ] Design durable native social-account credentials and per-platform publication records.
- [ ] Add a Python provider protocol and mocked transport tests without changing Upload-Post behavior.
- [ ] Implement and test YouTube native publishing.
- [ ] Implement and test Instagram native publishing.
- [ ] Implement and test TikTok Content Posting API, keeping public Direct Post disabled until audit requirements are met.
- [ ] Add durable scheduling, idempotency, reconciliation, and restart recovery.
- [ ] Connect dashboard account workflows and per-target publication states to real APIs.
- [ ] Run backend, frontend and end-to-end tests in a real checkout.
- [ ] Verify provider publication only with provider-approved test accounts.

## Current limitations

This branch contains integration planning and verified runtime mapping only. No native provider adapter, OAuth flow, credential store, durable publication ledger, scheduler, or dashboard workflow has been implemented. No backend/frontend tests or live provider tests have been run. Permission management and evidence storage are intentionally deferred and are not part of the current implementation scope.
