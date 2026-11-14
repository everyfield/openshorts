# Existing Publish Path Audit

Audit target: OpenShorts `main` at the time of inspection. This is a source-code audit, not a runtime test.

## Confirmed direct publishing path

| Entry point | Implementation | Risk / required integration |
|---|---|---|
| `POST /api/social/post` | `app.py::post_to_socials` | Sends the selected clip to Upload-Post. No creator permission record or approval check is read before the provider request. |
| Scheduled post | Same `POST /api/social/post` handler when `scheduled_date` is present | A scheduled post is accepted by the same unguarded path. Permission must be rechecked at schedule creation and immediately before execution. |
| Async upload status | `GET /api/social/post-status/{request_id}` | Reports provider-side asynchronous completion. It is not an authorization boundary and cannot replace a preflight permission check. |

The direct route resolves Upload-Post credentials/profile, checks job ownership and social-post rate limits, optionally checks account reconnection, then sends the file. None of those checks establishes the source creator's permission to edit or publish the video.

## Completion hook / autopilot

Completed jobs invoke `_autopilot_job_finished(job_id, job)`, which delegates to `cloud.autopilot.on_job_finished`. The cloud module was inspected. `cloud/autopilot.py::on_job_finished` selects clips when the user's `autopublish` preference is enabled, then calls `_publish`; `_publish` schedules each selected clip and calls `_upload_clip`. The Autopilot settings expose a general `rights_ack` acknowledgement, but this is not a per-source, per-platform creator approval record. The autopilot path therefore also lacks the new permission check.

## Required enforcement points

1. At clip-generation admission: require a source record and an explicit permission record for third-party material before starting processing.
2. At manual publish: verify permission against the exact source video, requested platform, editing scope, commercial-use scope, approval timestamp, and expiry/revocation state before calling Upload-Post.
3. At autopilot selection and scheduling: validate the exact source video and creator approval; a general Autopilot rights acknowledgement is not sufficient.
4. At scheduling: repeat the same validation before accepting a schedule.
5. At scheduled execution / autopilot: re-read permission immediately before provider submission. A stale approval cached at schedule creation is insufficient.
6. At provider status: keep each platform's result separate; HTTP acceptance/request ID is not a successful publication.
7. On revocation/expiry: cancel or block pending schedules and prevent retries.

## Current status

The foundation PR adds the policy primitive and PostgreSQL constraints, but neither is connected to the live route or cloud autopilot. The manual publish route and Autopilot publishing path therefore remain unguarded until integration is completed. Do not enable automated publishing or describe the workflow as permission-safe yet.
