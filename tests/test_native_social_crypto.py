"""Tests for KMS-backed credential encryption and tenant-bound AAD."""
from uuid import uuid4

import pytest

from native_social.crypto import CredentialEncryptionError, KMSCredentialCipher


class FakeKMS:
    def __init__(self):
        self.keys = {}

    def generate_data_key(self, *, KeyId, KeySpec, EncryptionContext):
        import os
        key = os.urandom(32)
        blob = b"fake-kms:" + key
        self.keys[blob] = (key, dict(EncryptionContext))
        return {"Plaintext": key, "CiphertextBlob": blob}

    def decrypt(self, *, CiphertextBlob, EncryptionContext, KeyId):
        key, context = self.keys[CiphertextBlob]
        if context != EncryptionContext:
            raise ValueError("KMS encryption context mismatch")
        return {"Plaintext": key}


def test_credentials_round_trip_without_plaintext_storage():
    kms = FakeKMS()
    cipher = KMSCredentialCipher(kms_client=kms, key_id="alias/native-social")
    owner, connection = uuid4(), uuid4()
    tokens = {"access_token": "secret-access", "refresh_token": "secret-refresh"}
    encrypted = cipher.encrypt(
        owner_id=owner, connection_id=connection, provider="youtube", tokens=tokens,
    )
    assert b"secret-access" not in encrypted.ciphertext
    assert b"secret-refresh" not in encrypted.ciphertext
    assert cipher.decrypt(
        owner_id=owner, connection_id=connection, provider="youtube", encrypted=encrypted,
    ) == {
        "access_token": "secret-access",
        "refresh_token": "secret-refresh",
        "token_type": "Bearer",
    }


def test_credentials_are_bound_to_owner_and_connection():
    kms = FakeKMS()
    cipher = KMSCredentialCipher(kms_client=kms, key_id="alias/native-social")
    owner, connection = uuid4(), uuid4()
    encrypted = cipher.encrypt(
        owner_id=owner, connection_id=connection, provider="instagram",
        tokens={"access_token": "secret"},
    )
    with pytest.raises(CredentialEncryptionError):
        cipher.decrypt(
            owner_id=uuid4(), connection_id=connection, provider="instagram",
            encrypted=encrypted,
        )


def test_missing_kms_configuration_fails_closed():
    cipher = KMSCredentialCipher(kms_client=FakeKMS(), key_id="")
    with pytest.raises(CredentialEncryptionError):
        cipher.encrypt(
            owner_id=uuid4(), connection_id=uuid4(), provider="tiktok",
            tokens={"access_token": "secret"},
        )


def test_unsupported_provider_is_rejected():
    cipher = KMSCredentialCipher(kms_client=FakeKMS(), key_id="alias/native-social")
    with pytest.raises(CredentialEncryptionError):
        cipher.encrypt(
            owner_id=uuid4(), connection_id=uuid4(), provider="unknown",
            tokens={"access_token": "secret"},
        )
