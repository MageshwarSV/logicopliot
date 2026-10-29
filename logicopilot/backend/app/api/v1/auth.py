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
    hash_password,
    hash_token,
    verify_password,
)
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    LogoutRequest,
    RefreshRequest,
    TokenPair,
)
from app.schemas.user import UserOut

# Every response here must carry the SAME derived/renamed fields the user list endpoint
# already gets right (modes <- assigned_modes, user_type_name, assigned_operator_ids,
# mail_connected) - UserOut.model_validate(user) alone only auto-maps same-named columns, so
# a bare call here was silently leaving the CURRENT session's own user object with modes=None
# and user_type_name=None regardless of its real assigned_modes/user_type, while every OTHER
# user (fetched through GET /users, which already calls this) had them correctly populated.
# Found live: a filter comparing "my own modes" against another user's real modes always
# compared against an empty set on my own side.
from app.api.v1.users import _user_out

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
        max_age=settings.refresh_token_expire_minutes * 60,
        path=AUTH_COOKIE_PATH,
    )


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_TOKEN_COOKIE, path="/")
    response.delete_cookie(REFRESH_TOKEN_COOKIE, path=AUTH_COOKIE_PATH)


def _token_pair(access_token: str, raw_refresh: str) -> TokenPair:
    """The same two tokens the cookies carry, handed to a client that cannot store cookies.

    Nothing new is minted here and nothing extra is granted: a Bearer access token is the
    identical JWT, checked by the identical code path (decode_token in get_current_user), and
    it expires on the identical clock. The only difference is where the client keeps it.
    """
    return TokenPair(
        access_token=access_token,
        refresh_token=raw_refresh,
        expires_in=settings.access_token_expire_minutes * 60,
        refresh_expires_in=settings.refresh_token_expire_minutes * 60,
    )


def _issue_tokens(db: Session, user: User, response: Response) -> tuple[str, str]:
    """Mints a token pair, persists the refresh token, sets the cookies, and returns the raw
    pair so a caller can ALSO hand it back in the body. Cookies are still set either way -
    they cost a native client nothing (it ignores them) and leave the web untouched."""
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
    return access_token, raw_refresh


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> LoginResponse:
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account is disabled")

    user.last_login_at = datetime.now(timezone.utc)
    db.add(user)
    access_token, raw_refresh = _issue_tokens(db, user, response)

    return LoginResponse(
        user=_user_out(user),
        tokens=_token_pair(access_token, raw_refresh) if payload.token_response else None,
    )


# A refresh token is rotated the instant it is used, so two TABS that both notice the access
# token has expired at the same moment each present the same still-valid token: the first
# rotates it, the second lands a few hundred milliseconds later and finds it already revoked.
# That is a race, not theft - and treating it as theft killed every session the user had.
# Found in the UAT token table: 11 tokens revoked in a single instant on 15 Oct, 10 on 13 Oct,
# and dozens of smaller bursts. This app opens each job in its OWN TAB, so several tabs per
# operator is simply how it is used, not an edge case.
#
# A genuinely stolen token still trips the full family revocation: it is either replayed long
# after it was rotated (outside this window), or it carries no replacement at all because a
# logout or an earlier family kill revoked it - neither can pass the checks below.
#
# BROWSER ONLY, deliberately. This applies to a token presented in the COOKIE; a token sent in
# the request BODY is the native app's (see refresh's own comment on the cookie-then-body
# order), and a phone has no second tab to race with. There is no race to forgive there, so
# the app keeps the original, strict rule: the second use of a rotated token is theft, full
# stop - which is exactly what test_a_replayed_app_refresh_token_is_rejected_like_any_other
# pins down.
_ROTATION_GRACE_SECONDS = 30


def _revoke_token_family(db: Session, user_id: str) -> None:
    """Reused refresh token = theft signal. Kill every session for this user."""
    now = datetime.now(timezone.utc)
    db.query(RefreshToken).filter(
        RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None)
    ).update({"revoked_at": now})
    db.commit()


@router.post("/refresh", response_model=LoginResponse)
def refresh(
    request: Request,
    response: Response,
    # NOT named `payload`: the decoded JWT below already owns that name in this function, and
    # shadowing it here made `payload.token_response` read a dict that has no such key.
    body: RefreshRequest | None = None,
    db: Session = Depends(get_db),
) -> LoginResponse:
    # Cookie first, body second - the web keeps posting no body at all and is unaffected. The
    # app has no cookie, so it sends the token it was given at login. Rotation, reuse
    # detection and family revocation below are shared by both: a stolen app token is killed
    # by exactly the same check that kills a stolen browser one.
    cookie_token = request.cookies.get(REFRESH_TOKEN_COOKIE)
    raw_token = cookie_token or (body.refresh_token if body else None)
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

    now = datetime.now(timezone.utc)
    rotation_race = False
    if stored.revoked_at is not None:
        # Postgres hands this back tz-aware; the sqlite test harness does not. Compare in UTC
        # either way rather than letting a naive value raise mid-request.
        revoked_at = stored.revoked_at
        if revoked_at.tzinfo is None:
            revoked_at = revoked_at.replace(tzinfo=timezone.utc)
        if (
            cookie_token is not None
            and stored.replaced_by_jti is not None
            and (now - revoked_at).total_seconds() <= _ROTATION_GRACE_SECONDS
        ):
            # Another tab rotated this very token a moment ago - see _ROTATION_GRACE_SECONDS.
            # Hand this tab its own fresh pair instead of killing the user's sessions. The
            # replacement the other tab received cannot be handed back (only its hash is
            # kept), so a new one is minted below and the original rotation chain is left
            # exactly as it is.
            rotation_race = True
        else:
            # Used/rotated long ago, or revoked by a logout or an earlier family kill — reuse
            # means theft. Nuke every active session for this user rather than trust it.
            _revoke_token_family(db, user_id)
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Refresh token reuse detected")

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")

    # Rotate: revoke the presented token, issue a fresh pair.
    new_access_token = create_access_token(user_id=user.id, role=user.role, tenant_id=user.tenant_id)
    new_raw_refresh, new_jti, new_expires_at = create_refresh_token(user_id=user.id)

    if not rotation_race:
        stored.revoked_at = now
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
    return LoginResponse(
        user=_user_out(user),
        tokens=(
            _token_pair(new_access_token, new_raw_refresh)
            if body is not None and body.token_response
            else None
        ),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    # `body`, not `payload`, for the same shadowing reason as refresh above.
    body: LogoutRequest | None = None,
    db: Session = Depends(get_db),
) -> None:
    # Same cookie-then-body order as refresh. Without this the app could only forget its token
    # locally, leaving the refresh token valid server-side until it expired on its own.
    raw_token = request.cookies.get(REFRESH_TOKEN_COOKIE) or (body.refresh_token if body else None)
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
    return _user_out(user)


@router.post("/change-password", response_model=UserOut)
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> UserOut:
    """Every role reaches this the same way: the forced one-time popup after an admin-chosen
    password (see User.must_change_password), or a voluntary change from Settings at any other
    time - both are the same action (the user proving they still know their current password,
    then picking their own), so there is exactly one endpoint for it."""
    if not verify_password(payload.current_password, user.hashed_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")
    user.hashed_password = hash_password(payload.new_password)
    user.must_change_password = False
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_out(user)
