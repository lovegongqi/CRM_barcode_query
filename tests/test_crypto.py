import base64

import pytest

from crm_storage.crypto import CredentialCipher


VALID_FERNET_KEY = base64.urlsafe_b64encode(b"c" * 32).decode("ascii")


def test_credentials_are_not_stored_as_plaintext():
    cipher = CredentialCipher.from_text_key("test-only-key-that-is-not-a-production-secret")

    token = cipher.encrypt({"username": "gongqi", "password": "secret"})

    assert b"secret" not in token
    assert cipher.decrypt(token)["password"] == "secret"


def test_postgresql_mode_requires_credentials_key(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://crm:test@127.0.0.1:55432/crm_test")
    monkeypatch.delenv("CRM_CREDENTIALS_KEY", raising=False)

    with pytest.raises(RuntimeError, match="CRM_CREDENTIALS_KEY"):
        CredentialCipher.from_env()


def test_environment_key_must_be_a_generated_fernet_key(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://crm:test@127.0.0.1:55432/crm_test")
    monkeypatch.setenv("CRM_CREDENTIALS_KEY", "test-only-key-that-is-not-a-production-secret")

    with pytest.raises(ValueError, match="32-byte URL-safe base64 Fernet key"):
        CredentialCipher.from_env()


def test_environment_key_uses_generated_fernet_key(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://crm:test@127.0.0.1:55432/crm_test")
    monkeypatch.setenv("CRM_CREDENTIALS_KEY", VALID_FERNET_KEY)

    cipher = CredentialCipher.from_env()

    assert cipher.decrypt(cipher.encrypt({"password": "secret"})) == {"password": "secret"}
