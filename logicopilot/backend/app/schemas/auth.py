from pydantic import BaseModel, EmailStr, field_validator

from app.schemas.user import UserOut


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    # Opt-in, and the native Manager app is the only caller that sets it. The browser leaves
    # it false and keeps using the httpOnly cookies set alongside, so the web's tokens stay
    # unreadable from JavaScript - that is the whole point of httpOnly, and it must not be
    # weakened for every existing user just because a second kind of client now exists.
    #
    # The app needs this because React Native has no cookie jar that survives the app being
    # closed, so it has to hold the token itself and present it as a Bearer header (see
    # _bearer_token in app/core/deps.py).
    token_response: bool = False


class TokenPair(BaseModel):
    """Only ever returned to a caller that asked for it (LoginRequest.token_response)."""

    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    # Seconds, not minutes, and not an absolute time: the phone's clock cannot be trusted to
    # agree with the server's, so the app counts down from when the response arrived.
    expires_in: int
    refresh_expires_in: int


class LoginResponse(BaseModel):
    user: UserOut
    # None for the web, which reads its tokens from cookies it never has to see.
    tokens: TokenPair | None = None


class RefreshRequest(BaseModel):
    """The whole body is optional, so the web's existing bodiless POST /auth/refresh is
    unchanged. The app sends the refresh token here because it has no cookie to send."""

    refresh_token: str | None = None
    token_response: bool = False


class LogoutRequest(BaseModel):
    """Same shape, same reason - the app has to say WHICH session to revoke, since it has no
    cookie for the endpoint to read. Optional, so the web's bodiless logout still works."""

    refresh_token: str | None = None


class ChangePasswordRequest(BaseModel):
    """Self-service only - the logged-in user changing their OWN password, whether to clear a
    forced must_change_password or just voluntarily from Settings. An admin resetting someone
    ELSE's password still goes through PATCH /users/{id} (UserUpdate.password)."""

    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def new_password_min_length(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("password must be at least 8 characters")
        return value
