"""Encrypt/decrypt an operator's mailbox app password at rest.

Keyed off the same jwt_secret_key every deployment already has to set — one less secret to
configure. Fernet needs a 32-byte urlsafe-base64 key, so the configured secret is hashed down
to exactly that shape rather than used directly.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


def _fernet() -> Fernet:
    settings = get_settings()
    key = hashlib.sha256(settings.jwt_secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_secret(ciphertext: str) -> str:
    """Raises InvalidToken if the stored value cannot be decrypted (wrong/rotated key)."""
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        raise
