"""KMS-backed envelope encryption for native social OAuth credentials.

No local/plaintext fallback exists. Production use requires an AWS KMS key with
GenerateDataKey/Decrypt permissions and NATIVE_SOCIAL_KMS_KEY_ID configured.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Mapping, Protocol
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class KMSClient(Protocol):
    def generate_data_key(self, **kwargs): ...
    def decrypt(self, **kwargs): ...


@dataclass(frozen=True, slots=True)
class EncryptedCredentials:
    encrypted_data_key: bytes
    nonce: bytes
    ciphertext: bytes
    kms_key_id: str
    key_version: int = 1


class CredentialEncryptionError(RuntimeError):
    """Raised when credentials cannot be encrypted or authenticated."""


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

    @staticmethod
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

    def encrypt(
        self, *, owner_id: UUID | str, connection_id: UUID | str,
        provider: str, tokens: Mapping[str, str],
    ) -> EncryptedCredentials:
        access_token = tokens.get("access_token")
        refresh_token = tokens.get("refresh_token")
        if not isinstance(access_token, str) or not access_token:
            raise CredentialEncryptionError("An access token is required")
        if refresh_token is not None and not isinstance(refresh_token, str):
            raise CredentialEncryptionError("Invalid refresh token")
        context = self._context(owner_id, connection_id, provider)
        try:
            response = self._client().generate_data_key(
                KeyId=self._key_id, KeySpec="AES_256", EncryptionContext=context,
            )
            data_key = response["Plaintext"]
            nonce = os.urandom(12)
            plaintext = json.dumps({
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": tokens.get("token_type", "Bearer"),
            }, separators=(",", ":"), sort_keys=True).encode("utf-8")
            ciphertext = AESGCM(data_key).encrypt(nonce, plaintext, json.dumps(
                context, sort_keys=True, separators=(",", ":")
            ).encode("utf-8"))
            return EncryptedCredentials(
                encrypted_data_key=response["CiphertextBlob"],
                nonce=nonce,
                ciphertext=ciphertext,
                kms_key_id=self._key_id,
            )
        except CredentialEncryptionError:
            raise
        except Exception as exc:
            raise CredentialEncryptionError("Credential encryption failed") from exc

    def decrypt(
        self, *, owner_id: UUID | str, connection_id: UUID | str,
        provider: str, encrypted: EncryptedCredentials,
    ) -> dict[str, str | None]:
        context = self._context(owner_id, connection_id, provider)
        try:
            response = self._client().decrypt(
                CiphertextBlob=encrypted.encrypted_data_key,
                EncryptionContext=context,
                KeyId=encrypted.kms_key_id,
            )
            aad = json.dumps(context, sort_keys=True, separators=(",", ":")).encode("utf-8")
            plaintext = AESGCM(response["Plaintext"]).decrypt(
                encrypted.nonce, encrypted.ciphertext, aad
            )
            values = json.loads(plaintext)
            if not isinstance(values, dict) or not isinstance(values.get("access_token"), str):
                raise ValueError("Invalid credential payload")
            return {
                "access_token": values["access_token"],
                "refresh_token": values.get("refresh_token"),
                "token_type": values.get("token_type", "Bearer"),
            }
        except Exception as exc:
            raise CredentialEncryptionError("Credential decryption/authentication failed") from exc
