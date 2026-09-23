import hashlib
import uuid
from datetime import datetime, timedelta, timezone
try:
    from enum import StrEnum
except ImportError:  # Python < 3.11
    from enum import Enum

    class StrEnum(str, Enum):
        pass
from typing import Any

import jwt
from passlib.context import CryptContext

from app.core.config import get_settings

settings = get_settings()
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


class TokenType(StrEnum):
    ACCESS = "access"
    REFRESH = "refresh"


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def hash_token(raw_token: str) -> str:
    """SHA-256 digest used to store refresh tokens without keeping the raw value."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def create_access_token(*, user_id: str, role: str, tenant_id: str | None) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role,
        "tenant_id": tenant_id,
        "jti": str(uuid.uuid4()),
        "type": TokenType.ACCESS.value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def create_refresh_token(*, user_id: str) -> tuple[str, str, datetime]:
    """Returns (raw_token, jti, expires_at). Caller persists jti/hash to the refresh_tokens table."""
    now = datetime.now(timezone.utc)
    jti = str(uuid.uuid4())
    expires_at = now + timedelta(days=settings.refresh_token_expire_days)
    payload = {
        "sub": user_id,
        "jti": jti,
        "type": TokenType.REFRESH.value,
        "iat": now,
        "exp": expires_at,
    }
    raw_token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return raw_token, jti, expires_at


def decode_token(token: str) -> dict[str, Any]:
    """Raises jwt.PyJWTError (or a subclass) on invalid/expired tokens — callers should catch it."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
