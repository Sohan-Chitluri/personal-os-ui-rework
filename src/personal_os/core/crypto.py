"""Encryption helper for sensitive credentials (e.g. GitHub access tokens).

Uses AES-128-CBC + HMAC-SHA256 authenticated encryption via cryptography.fernet.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet

from personal_os.config import get_settings


def _derive_fernet_key(raw_key: str) -> bytes:
    """Derive a valid urlsafe base64-encoded 32-byte Fernet key from any string."""
    digest = hashlib.sha256(raw_key.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def get_encryption_key(explicit_key: str | None = None) -> bytes:
    if explicit_key:
        return _derive_fernet_key(explicit_key)
    settings = get_settings()
    source = (
        getattr(settings, "github_token_encryption_key", None)
        or settings.companion_jwt_secret
        or settings.dev_auth_token
        or "personal-os-default-development-encryption-key-seed"
    )
    return _derive_fernet_key(source)


def encrypt_secret(plaintext: str, key: str | None = None) -> str:
    """Encrypt a secret string and return a base64-encoded ciphertext."""
    if not plaintext:
        return ""
    fernet = Fernet(get_encryption_key(key))
    encrypted_bytes = fernet.encrypt(plaintext.encode("utf-8"))
    return encrypted_bytes.decode("ascii")


def decrypt_secret(ciphertext: str, key: str | None = None) -> str:
    """Decrypt a base64-encoded ciphertext back to the original plaintext string."""
    if not ciphertext:
        return ""
    fernet = Fernet(get_encryption_key(key))
    decrypted_bytes = fernet.decrypt(ciphertext.encode("ascii"))
    return decrypted_bytes.decode("utf-8")
