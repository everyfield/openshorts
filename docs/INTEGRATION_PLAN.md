# Clipping and social publishing integration

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

## Delivery sequence

- [x] Inspect the two upstream READMEs, licenses, runtime manifests and OpenShorts API entrypoint.
- [x] Confirm the OpenShorts fork and create an isolated feature branch.
- [x] Document the integration architecture and licensing constraints.
- [ ] Map the existing FastAPI job lifecycle and publishing paths.
- [ ] Replace/extend Upload-Post with separately modeled official provider adapters.
- [ ] Add scheduling and per-platform publishing state based on the existing application architecture.
- [ ] Connect UI pages to real APIs and job states.
- [ ] Run backend, frontend and end-to-end tests in a real checkout.
- [ ] Verify provider publication only with approved test accounts.

## Current limitations

This branch currently contains integration planning/documentation only. No runtime publishing path has been changed, no official provider adapter has been completed, and no provider credentials or live social accounts have been tested. Permission management and evidence storage are intentionally deferred and are not part of the current implementation scope.
