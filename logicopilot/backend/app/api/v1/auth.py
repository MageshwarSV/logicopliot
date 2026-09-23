from datetime import datetime, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.deps import ACCESS_TOKEN_COOKIE, REFRESH_TOKEN_COOKIE, get_current_user, get_db
from app.core.security import (
    TokenType,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_token,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse
from app.schemas.user import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()

AUTH_COOKIE_PATH = "/api/v1/auth"


def _set_auth_cookies(response: Response, access_token: str, refresh_token: str) -> None:
    response.set_cookie(
        ACCESS_TOKEN_COOKIE,
        access_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.access_token_expire_minutes * 60,
        path="/",
    )
    response.set_cookie(
        REFRESH_TOKEN_COOKIE,
        refresh_token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        path=AUTH_COOKIE_PATH,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    response.delete_cookie(REFRESH_TOKEN_COOKIE, path=AUTH_COOKIE_PATH)


def _issue_tokens(db: Session, user: User, response: Response) -> None:
    access_token = create_access_token(user_id=user.id, role=user.role, tenant_id=user.tenant_id)
    raw_refresh, jti, expires_at = create_refresh_token(user_id=user.id)
    db.add(
        RefreshToken(
            user_id=user.id,
            jti=jti,
            token_hash=hash_token(raw_refresh),
            expires_at=expires_at,
        )
    )
    db.commit()
    _set_auth_cookies(response, access_token, raw_refresh)


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is disabled")

    user.last_login_at = datetime.now(timezone.utc)
    db.add(user)
    _issue_tokens(db, user, response)

    return LoginResponse(user=UserOut.model_validate(user))


def _revoke_token_family(db: Session, user_id: str) -> None:
    """Reused refresh token = theft signal. Kill every session for this user."""
    now = datetime.now(timezone.utc)
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    ).update({"revoked_at": now})
    db.commit()


@router.post("/refresh", response_model=LoginResponse)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> LoginResponse:
    raw_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")

    try:
        payload = decode_token(raw_token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")

    if payload.get("type") != TokenType.REFRESH.value:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type")

    stored = db.query(RefreshToken).filter(RefreshToken.jti == payload.get("jti")).first()
    if stored is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token not recognized")

    user_id = payload.get("sub")

    if stored.revoked_at is not None:
        # This token was already used/rotated once before — reuse means theft. Nuke every
        # active session for this user rather than trusting this request.
        _revoke_token_family(db, user_id)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token reuse detected")

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Rotate: revoke the presented token, issue a fresh pair.
    new_access_token = create_access_token(user_id=user.id, role=user.role, tenant_id=user.tenant_id)
    new_raw_refresh, new_jti, new_expires_at = create_refresh_token(user_id=user.id)

    stored.revoked_at = datetime.now(timezone.utc)
    stored.replaced_by_jti = new_jti
    db.add(stored)
    db.add(
        RefreshToken(
            user_id=user.id,
            jti=new_jti,
            token_hash=hash_token(new_raw_refresh),
            expires_at=new_expires_at,
        )
    )
    db.commit()

    _set_auth_cookies(response, new_access_token, new_raw_refresh)
    return LoginResponse(user=UserOut.model_validate(user))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    raw_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    if raw_token:
        try:
            payload = decode_token(raw_token)
            stored = db.query(RefreshToken).filter(RefreshToken.jti == payload.get("jti")).first()
            if stored is not None and stored.revoked_at is None:
                stored.revoked_at = datetime.now(timezone.utc)
                db.add(stored)
                db.commit()
        except jwt.PyJWTError:
            pass  # Already-invalid token — nothing to revoke, just clear cookies below.

    _clear_auth_cookies(response)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
