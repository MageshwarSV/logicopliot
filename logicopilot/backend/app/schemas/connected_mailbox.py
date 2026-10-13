from pydantic import BaseModel, EmailStr, field_validator

MAIL_PROVIDERS = ("gmail", "zoho")


class ConnectedMailboxOut(BaseModel):
    model_config = {"from_attributes": True}

    id: str
    provider: str
    email: str
    tenant_id: str | None
    tenant_name: str | None = None
    group_id: str | None
    group_name: str | None = None
    paused: bool


class ConnectMailboxIn(BaseModel):
    email: EmailStr
    app_password: str
    # Omitted = auto-detect (tries every known host for both providers - see
    # _detect_and_verify_mailbox in users.py, reused as-is here).
    provider: str | None = None

    @field_validator("provider")
    @classmethod
    def _provider_must_be_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in MAIL_PROVIDERS:
            raise ValueError(f"provider must be one of {MAIL_PROVIDERS}")
        return v


class UpdateMailboxIn(BaseModel):
    """Every field optional: only what is sent is changed. Sending tenant_id (even to the
    SAME value) when a group_id is already set is the caller's own responsibility to pair
    correctly - the endpoint clears group_id for you only when tenant_id actually changes."""

    tenant_id: str | None = None
    group_id: str | None = None
    paused: bool | None = None
