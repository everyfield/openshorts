# Self-hosted Instagram publishing

This mode stores application data, Instagram credentials, publish jobs, and video files on the machine running OpenShorts. It does not use Cloudflare R2, S3, AWS KMS, or a hosted publishing service.

Instagram itself remains an external API. Meta must be able to fetch the finished MP4 over public HTTPS; the file is served directly from the local disk through Caddy using a short-lived HMAC-signed URL.

## Requirements

- A server or PC that can remain online while publishing.
- A domain with an A/AAAA record pointing to that machine.
- Inbound TCP 80 and 443 reachable so Caddy can obtain and renew HTTPS certificates.
- Docker Engine and Docker Compose.
- An Instagram Professional account (Business or Creator).
- A Meta developer app configured for Instagram Login.

The app and media remain self-hosted, but a public domain and internet access are unavoidable for Meta OAuth and media retrieval.

## Configure

1. Copy `.env.example` to `.env`.
2. Set `DOMAIN` to your public hostname and point its DNS record to your server.
3. Set `POSTGRES_PASSWORD` to a unique random password.
4. Set `AUTH_USER` and generate `AUTH_PASSWORD_HASH`:

   ```bash
   docker run --rm caddy:2.10-alpine caddy hash-password --plaintext 'YOUR_LONG_PASSWORD'
   ```

5. Generate two independent 32-byte keys. Run the command twice and use a different output for each variable:

   ```bash
   python -c "import secrets,base64; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('='))"
   ```

   - Put the first value in `NATIVE_SOCIAL_ENCRYPTION_KEY`.
   - Put the second value in `NATIVE_SOCIAL_MEDIA_SIGNING_KEY`.

6. Set:
   - `NATIVE_SOCIAL_ENABLED=1`
   - `NATIVE_SOCIAL_ENCRYPTION_MODE=local`
   - `NATIVE_SOCIAL_PUBLIC_BASE_URL=https://YOUR_DOMAIN`
   - `NATIVE_SOCIAL_FRONTEND_URL=https://YOUR_DOMAIN`
   - `INSTAGRAM_APP_ID` and `INSTAGRAM_APP_SECRET`
   - `INSTAGRAM_REDIRECT_URI=https://YOUR_DOMAIN/api/native-social/instagram/callback`

Keep both cryptographic keys and the PostgreSQL password outside source control. Back them up securely. Losing the encryption key makes stored Instagram tokens unrecoverable; losing the media-signing key invalidates any media URLs already issued.

## Meta configuration

In the Meta developer app, configure Instagram Login and add this exact OAuth redirect URI:

```
https://YOUR_DOMAIN/api/native-social/instagram/callback
```

Request only the scopes used by this implementation:

- `instagram_business_basic`
- `instagram_business_content_publish`

The Instagram account must be a Professional account. Meta may require app review or access approval before accounts outside the app's developer/tester roles can connect.

## Start

```bash
docker compose up -d --build
docker compose logs -f backend caddy
```

Open `https://YOUR_DOMAIN`. Caddy prompts for the configured HTTP Basic Auth credentials. The dashboard and private API routes are protected. Only the OAuth callback and signed media endpoint are public so Meta can complete OAuth and fetch a Reel.

Put publishable MP4 files under the local `output/` directory. The Instagram clip picker discovers MP4 files there. The worker stores job state and encrypted credentials in the local PostgreSQL volume.

## Local persistence and backups

- Videos: `./output/`
- PostgreSQL: Docker volume `openshorts_postgres`
- HTTPS certificates/config: Docker volumes `openshorts_caddy_data` and `openshorts_caddy_config`
- OAuth encryption key: `NATIVE_SOCIAL_ENCRYPTION_KEY` in `.env`
- Media URL signing key: `NATIVE_SOCIAL_MEDIA_SIGNING_KEY` in `.env`

Back up `output/`, PostgreSQL, and `.env` together. Keep key backups offline or in a separate encrypted backup. A PostgreSQL dump can be made with:

```bash
docker compose exec -T postgres pg_dump -U openshorts openshorts > openshorts.sql
```

Do not publish `.env`, database dumps, or OAuth credentials.

## Important boundaries

- No R2/S3 upload is performed in this mode.
- No AWS KMS call is made in local encryption mode.
- The MP4 remains on local disk; Meta downloads it from the temporary HTTPS endpoint.
- The media URL expires after 30 minutes.
- The Instagram worker uses PostgreSQL polling; Redis is not required by this implementation.
- The self-hosted Instagram mode is single-installation/single-owner, not multi-tenant.
- Do not expose ports 8000 or 3100 directly to the internet. Use the Caddy entry point and keep 80/443 open for HTTPS.
