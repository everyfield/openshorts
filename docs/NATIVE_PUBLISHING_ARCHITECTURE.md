# Native publishing persistence and UI architecture

Inspection target: `everyfield/openshorts` at `5b630d3082248d3a6364f049a986dc6129ef3a56`.

## Findings

### Persistence boundary
- The inspected core tree has no independent, MIT-core social-account or publishing database model/migration set.
- The root `alembic/env.py` imports `cloud.database.Base` and `cloud.models`. The `cloud/` directory is separately commercially licensed and excluded from this integration.
- Therefore, using the existing Alembic setup for a new core publishing schema would couple the MIT core to the separately licensed cloud package. Do not do this.
- Existing `cloud.social_profiles`, `cloud.autopilot`, and `cloud.models` must not be imported or copied into the core implementation.
- The Upload-Post integration in `app.py` is the current social-account and publishing boundary. Its managed-account profile behavior is cloud-specific; native provider credentials must not be inferred from those profile IDs.

### Dashboard
- `dashboard/src/components/AccountPage.jsx` contains account/billing UI and embeds the social analytics card.
- `dashboard/src/components/AutopilotTab.jsx` is the existing Autopilot UI, but it is coupled to `/api/autopilot`, the Upload-Post connection flow, and cloud Autopilot endpoints. It is not a neutral native-publishing account manager.
- `dashboard/src/lib/api.js` centralizes authenticated API requests using the existing bearer session token.
- Do not silently replace Autopilot or the Upload-Post account page. Native provider connections should be a distinct, explicit section until parity is proven.

### Existing tests
- `tests/test_social_profile_create.py` tests cloud Upload-Post profile creation and idempotency via `cloud.social_profiles`.
- `tests/test_social_tenant_isolation.py` pins managed Upload-Post profile isolation and scheduled-list filtering.
- These tests are useful regression references, but they do not test native OAuth, native provider accounts, or a durable native publication ledger.

## Recommended core-compatible persistence design

The repository needs a persistence boundary that does not depend on `cloud/`. Before implementation, choose and document the core runtime database contract (driver, connection configuration, migration runner, and supported deployment mode). Do not assume PostgreSQL is configured in the open-source core merely because cloud mode uses it.

Once that boundary exists, use owner-scoped records with the following minimum logical entities:

### `social_connections`
- `id` UUID primary key
- `owner_id` stable authenticated principal identifier
- `provider` constrained to `youtube`, `instagram`, `tiktok`
- `provider_account_id` and display metadata (no secrets)
- `status` (`connected`, `reauth_required`, `revoked`)
- `created_at`, `updated_at`, `last_validated_at`
- Unique constraint on owner/provider/provider-account identity.

### `social_credentials`
- `connection_id` primary/foreign key
- encrypted access token and refresh token
- encryption key version, token expiry, scopes, refresh timestamp
- never return token material through API responses
- encryption/decryption must be provided by an explicit secret-management interface; no plaintext fallback and no hard-coded key.

### `publish_jobs`
- `id` UUID primary key
- `owner_id`, `source_job_id`, `media_reference`, caption, scheduled time
- `status`, created/updated timestamps
- client-supplied idempotency key with unique owner-scoped constraint.

### `publish_targets`
- `id` UUID primary key
- `publish_job_id`, `connection_id`, provider
- `status`, provider operation/post ID, attempt count
- sanitized error code/message, started/completed timestamps
- unique constraint on job + connection.

The logical model is intentionally provider-neutral. Do not persist raw media bytes in these tables; use the existing job/media storage boundary and short-lived provider upload URLs where supported.

## State and retry rules

- Target states: `pending`, `queued`, `publishing`, `processing`, `published`, `failed`, `needs_review`, `cancelled`.
- Persist `publishing` before making a non-idempotent provider write.
- A timeout after request transmission is ambiguous. Reconcile using provider operation IDs/status APIs where possible; otherwise set `needs_review`. Never blindly retry an ambiguous create/publish call.
- Retries are allowed only for classified transient failures where the provider contract makes the operation safe to retry.
- A target's failure must not overwrite another target's success.
- Every read/write query must be scoped by authenticated owner; never accept owner IDs from request bodies as authority.

## API boundary

Proposed endpoints (not implemented):
- `GET /api/native-social/connections`
- `POST /api/native-social/{provider}/oauth/start`
- `GET /api/native-social/{provider}/oauth/callback`
- `DELETE /api/native-social/connections/{connection_id}`
- `POST /api/native-publishing/jobs`
- `GET /api/native-publishing/jobs/{job_id}`
- `GET /api/native-publishing/jobs/{job_id}/targets`

OAuth callback requirements: cryptographically random single-use state, server-side state binding to owner/provider/redirect URI, PKCE where supported, strict redirect URI allowlist, state expiry, replay protection, exact scope validation, and token exchange only on the server. Provider credentials and callback codes must never enter browser local storage, logs, or analytics.

## UI boundary

Add a separate dashboard section for native social connections and native publishing. It should:
- list connected provider accounts and connection health;
- show per-platform publish status independently;
- show sanitized actionable errors and reconnect state;
- allow scheduling only when the selected account and media format are supported;
- keep Upload-Post and existing Autopilot UI operational and clearly separate.

## Delivery gates

1. Decide and implement a core persistence/migration contract without importing `cloud/`.
2. Add schema + repository layer and owner-isolation tests.
3. Add credential encryption interface and tests proving no plaintext token response/logging.
4. Add provider protocol + mock transport contract tests.
5. Implement one provider at a time, beginning with YouTube.
6. Add dashboard UI only after API contracts are stable.
7. Run the repository test suite in a real checkout; GitHub file inspection alone is not test execution.

## Explicit exclusions

No creator outreach, permission request automation, approval workflow, evidence upload, or permission/evidence storage. No modifications to `cloud/` or its separately licensed code. No automatic live publishing until the relevant provider app/account approval and integration tests are complete.


## Implementation status update (2026-11-15)

The following foundation is now implemented on the feature branch:
- `native_social/models.py` defines isolated native connection, encrypted credential, publish job and per-platform target tables.
- `native_social/persistence.py` initializes only those tables using its own SQLAlchemy metadata and provides owner-scoped credential store/load operations.
- `native_social/crypto.py` implements KMS envelope encryption with AES-256-GCM and tenant/provider-bound authenticated context.
- `app.py` initializes the isolated persistence pool only in hosted/billing mode and disposes it at shutdown. The existing cloud DB engine and cloud models are not imported by the native-social persistence package.
- `tests/test_native_social_crypto.py` adds four encryption tests.

Remaining limitations:
- The database schema currently bootstraps new tables with SQLAlchemy `create_all`; a versioned migration runner is not implemented. `create_all` will not alter existing tables.
- OAuth routes, provider adapters, refresh rotation, revoke flow, API endpoints and dashboard UI remain pending.
- No PostgreSQL integration tests or real-checkout test execution has been performed.
- Set `NATIVE_SOCIAL_KMS_KEY_ID` and grant the service role KMS `GenerateDataKey` and `Decrypt` permissions. Without KMS, token encryption/decryption fails closed.
