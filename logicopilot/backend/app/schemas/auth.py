from pydantic import BaseModel, EmailStr, field_validator

from app.schemas.user import UserOut


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    user: UserOut


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
