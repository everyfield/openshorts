"""Local-disk media delivery for Instagram's remote media fetcher.

Media stays on the self-hosted machine. Instagram receives a short-lived,
HMAC-signed HTTPS URL that streams the file directly from this application.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from pathlib import Path
from urllib.parse import urlsplit


class LocalMediaError(RuntimeError):
    pass


def _key() -> bytes:
    raw = os.environ.get("NATIVE_SOCIAL_MEDIA_SIGNING_KEY", "").strip()
    if not raw:
        raise LocalMediaError("NATIVE_SOCIAL_MEDIA_SIGNING_KEY is required")
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except Exception as exc:
        raise LocalMediaError("Media signing key must be URL-safe base64") from exc
    if len(key) < 32:
        raise LocalMediaError("Media signing key must decode to at least 32 bytes")
    return key


def _root() -> Path:
    root = Path(os.environ.get("NATIVE_SOCIAL_MEDIA_DIR", "output")).expanduser()
    return root.resolve()


def resolve_local_media_path(relative_path: str) -> Path:
    if (not relative_path or len(relative_path) > 1024 or "\x00" in relative_path
            or "\\" in relative_path):
        raise LocalMediaError("Invalid local media path")
    candidate = Path(relative_path)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise LocalMediaError("Media path must be a normalized relative path")
    root = _root()
    try:
        resolved = (root / candidate).resolve(strict=True)
        resolved.relative_to(root)
    except FileNotFoundError as exc:
        raise LocalMediaError("Local media file does not exist") from exc
    except ValueError as exc:
        raise LocalMediaError("Media path escapes the local media directory") from exc
    except OSError as exc:
        raise LocalMediaError("Local media file cannot be accessed") from exc
    if not resolved.is_file():
        raise LocalMediaError("Local media file does not exist")
    return resolved



def list_local_media_files(*, limit: int = 100) -> list[tuple[str, Path]]:
    """List publishable MP4 files beneath the configured local media root."""
    root = _root()
    if not root.exists():
        return []
    found: list[tuple[str, Path]] = []
    for candidate in root.rglob("*"):
        if not candidate.is_file() or candidate.suffix.lower() != ".mp4":
            continue
        try:
            resolved = candidate.resolve(strict=True)
            relative = resolved.relative_to(root).as_posix()
            safe = resolve_local_media_path(relative)
        except (OSError, ValueError, LocalMediaError):
            continue
        found.append((relative, safe))
    found.sort(key=lambda item: item[1].stat().st_mtime, reverse=True)
    return found[:max(1, min(limit, 500))]


def create_signed_local_media_url(relative_path: str, *, ttl_seconds: int = 1800) -> str:
    base = os.environ.get("NATIVE_SOCIAL_PUBLIC_BASE_URL", "").strip().rstrip("/")
    parsed = urlsplit(base)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise LocalMediaError("NATIVE_SOCIAL_PUBLIC_BASE_URL must be an HTTPS origin")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise LocalMediaError("Public base URL must be an origin without a path")
    # Validate before issuing a URL so nonexistent files fail before contacting Meta.
    resolve_local_media_path(relative_path)
    expiry = int(time.time()) + max(60, min(int(ttl_seconds), 3600))
    encoded_path = base64.urlsafe_b64encode(relative_path.encode("utf-8")).decode("ascii").rstrip("=")
    payload = f"{encoded_path}.{expiry}"
    signature = hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{base}/api/native-social/instagram/media/{payload}.{signature}"


def resolve_signed_media_token(token: str) -> Path:
    if not token or len(token) > 4096:
        raise LocalMediaError("Invalid media token")
    try:
        encoded_path, expiry_text, supplied_signature = token.rsplit(".", 2)
        expiry = int(expiry_text)
        payload = f"{encoded_path}.{expiry_text}"
        expected = hmac.new(_key(), payload.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied_signature):
            raise LocalMediaError("Invalid media signature")
        if expiry < int(time.time()):
            raise LocalMediaError("Media URL has expired")
        relative_path = base64.urlsafe_b64decode(
            encoded_path + "=" * (-len(encoded_path) % 4)
        ).decode("utf-8")
        return resolve_local_media_path(relative_path)
    except LocalMediaError:
        raise
    except Exception as exc:
        raise LocalMediaError("Invalid media token") from exc
