"""Credential encryption backends for native social OAuth credentials.

Hosted mode can use AWS KMS envelope encryption. Self-hosted mode can use a
single installation-owned 256-bit key; no network KMS dependency is required.
"""
from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from typing import Mapping, Protocol
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialCipher(Protocol):
    def encrypt(
        self, *, owner_id: UUID | str, connection_id: UUID | str,
        provider: str, tokens: Mapping[str, str],
    ) -> "EncryptedCredentials": ...

    def decrypt(
        self, *, owner_id: UUID | str, connection_id: UUID | str,
        provider: str, encrypted: "EncryptedCredentials",
    ) -> dict[str, str | None]: ...


class KMSClient(Protocol):
    def generate_data_key(self, **kwargs): ...
    def decrypt(self, **kwargs): ...


@dataclass(frozen=True, slots=True)
class EncryptedCredentials:
    encrypted_data_key: bytes
    nonce: bytes
    ciphertext: bytes
    # Retained for DB compatibility. "local:v1" identifies local-key records.
    kms_key_id: str
    key_version: int = 1


class CredentialEncryptionError(RuntimeError):
    """Raised when credentials cannot be encrypted or authenticated."""


def _context(owner_id: UUID | str, connection_id: UUID | str, provider: str) -> dict[str, str]:
    provider = provider.strip().lower()
    if provider not in {"youtube", "instagram", "tiktok"}:
        raise CredentialEncryptionError("Unsupported social provider")
    return {
        "owner_id": str(owner_id),
        "connection_id": str(connection_id),
        "provider": provider,
        "purpose": "native-social-oauth-v1",
    }


def _aad(context: dict[str, str]) -> bytes:
    return json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _serialize_tokens(tokens: Mapping[str, str]) -> bytes:
    access_token = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")
    if not isinstance(access_token, str) or not access_token:
        raise CredentialEncryptionError("An access token is required")
    if refresh_token is not None and not isinstance(refresh_token, str):
        raise CredentialEncryptionError("Invalid refresh token")
    return json.dumps({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": tokens.get("token_type", "Bearer"),
    }, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _deserialize_tokens(plaintext: bytes) -> dict[str, str | None]:
    values = json.loads(plaintext)
    if not isinstance(values, dict) or not isinstance(values.get("access_token"), str):
        raise ValueError("Invalid credential payload")
    return {
        "access_token": values["access_token"],
        "refresh_token": values.get("refresh_token"),
        "token_type": values.get("token_type", "Bearer"),
    }


class KMSCredentialCipher:
    """AES-256-GCM envelope encryption with KMS-managed data keys."""

    def __init__(self, *, kms_client: KMSClient | None = None, key_id: str | None = None):
        self._kms = kms_client
        self._key_id = (key_id or os.environ.get("NATIVE_SOCIAL_KMS_KEY_ID", "")).strip()

    def _client(self) -> KMSClient:
        if not self._key_id:
            raise CredentialEncryptionError("NATIVE_SOCIAL_KMS_KEY_ID is required")
        if self._kms is None:
            import boto3
            self._kms = boto3.client("kms")
        return self._kms

    def encrypt(self, *, owner_id, connection_id, provider, tokens) -> EncryptedCredentials:
        context = _context(owner_id, connection_id, provider)
        try:
            response = self._client().generate_data_key(
                KeyId=self._key_id, KeySpec="AES_256", EncryptionContext=context,
            )
            nonce = os.urandom(12)
            ciphertext = AESGCM(response["Plaintext"]).encrypt(
                nonce, _serialize_tokens(tokens), _aad(context),
            )
            return EncryptedCredentials(
                encrypted_data_key=response["CiphertextBlob"], nonce=nonce,
                ciphertext=ciphertext, kms_key_id=response.get("KeyId") or self._key_id,
            )
        except CredentialEncryptionError:
            raise
        except Exception as exc:
            raise CredentialEncryptionError("Credential encryption failed") from exc

    def decrypt(self, *, owner_id, connection_id, provider, encrypted) -> dict[str, str | None]:
        context = _context(owner_id, connection_id, provider)
        try:
            response = self._client().decrypt(
                CiphertextBlob=encrypted.encrypted_data_key,
                EncryptionContext=context, KeyId=encrypted.kms_key_id,
            )
            plaintext = AESGCM(response["Plaintext"]).decrypt(
                encrypted.nonce, encrypted.ciphertext, _aad(context),
            )
            return _deserialize_tokens(plaintext)
        except Exception as exc:
            raise CredentialEncryptionError("Credential decryption/authentication failed") from exc


class LocalCredentialCipher:
    """AES-256-GCM encryption using a locally managed installation key.

    NATIVE_SOCIAL_ENCRYPTION_KEY must be URL-safe base64 encoding of exactly
    32 random bytes. The key is never generated implicitly or stored in the DB.
    Back it up separately; losing it makes saved OAuth credentials unrecoverable.
    """

    KEY_ID = "local:v1"

    def __init__(self, *, key: bytes | None = None):
        if key is None:
            raw = os.environ.get("NATIVE_SOCIAL_ENCRYPTION_KEY", "").strip()
            if not raw:
                raise CredentialEncryptionError("NATIVE_SOCIAL_ENCRYPTION_KEY is required")
            try:
                key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            except Exception as exc:
                raise CredentialEncryptionError("Encryption key must be URL-safe base64") from exc
        if len(key) != 32:
            raise CredentialEncryptionError("Local encryption key must decode to exactly 32 bytes")
        self._key = key

    def encrypt(self, *, owner_id, connection_id, provider, tokens) -> EncryptedCredentials:
        context = _context(owner_id, connection_id, provider)
        try:
            nonce = os.urandom(12)
            ciphertext = AESGCM(self._key).encrypt(
                nonce, _serialize_tokens(tokens), _aad(context),
            )
            return EncryptedCredentials(
                encrypted_data_key=b"", nonce=nonce, ciphertext=ciphertext,
                kms_key_id=self.KEY_ID,
            )
        except CredentialEncryptionError:
            raise
        except Exception as exc:
            raise CredentialEncryptionError("Credential encryption failed") from exc

    def decrypt(self, *, owner_id, connection_id, provider, encrypted) -> dict[str, str | None]:
        if encrypted.kms_key_id != self.KEY_ID or encrypted.encrypted_data_key:
            raise CredentialEncryptionError("Credential record uses a different encryption backend")
        context = _context(owner_id, connection_id, provider)
        try:
            plaintext = AESGCM(self._key).decrypt(
                encrypted.nonce, encrypted.ciphertext, _aad(context),
            )
            return _deserialize_tokens(plaintext)
        except Exception as exc:
            raise CredentialEncryptionError("Credential decryption/authentication failed") from exc


def get_credential_cipher() -> CredentialCipher:
    """Select an explicit backend; never silently fall back between key stores."""
    mode = os.environ.get("NATIVE_SOCIAL_ENCRYPTION_MODE", "kms").strip().lower()
    if mode == "local":
        return LocalCredentialCipher()
    if mode == "kms":
        return KMSCredentialCipher()
    raise CredentialEncryptionError("NATIVE_SOCIAL_ENCRYPTION_MODE must be 'local' or 'kms'")
