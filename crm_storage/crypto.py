import base64
import hashlib
import json
import os

from cryptography.fernet import Fernet


class CredentialCipher:
    def __init__(self, fernet):
        self._fernet = fernet

    @classmethod
    def from_text_key(cls, text_key):
        key = base64.urlsafe_b64encode(hashlib.sha256(text_key.encode("utf-8")).digest())
        return cls(Fernet(key))

    @classmethod
    def from_env(cls, require_key=None):
        text_key = os.environ.get("CRM_CREDENTIALS_KEY")
        if text_key:
            return cls.from_text_key(text_key)
        postgresql_mode = require_key if require_key is not None else bool(os.environ.get("DATABASE_URL"))
        if postgresql_mode:
            raise RuntimeError("CRM_CREDENTIALS_KEY is required in PostgreSQL mode")
        return None

    def encrypt(self, credentials):
        payload = json.dumps(credentials, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(payload)

    def decrypt(self, token):
        return json.loads(self._fernet.decrypt(token))
