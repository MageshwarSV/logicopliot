from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, field_validator

from app.core.modes import MODES
from app.models.user import ROLES


MAIL_PROVIDERS = ("gmail", "zoho")


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    full_name: str
    role: str
    tenant_id: str | None
    is_active: bool
    last_login_at: datetime | None
    mail_provider: str | None = None
    # The exact IMAP host verified (matters for Zoho's regional data centers) - informational.
    mail_host: str | None = None
    mail_email: str | None = None
    # Never the password itself — just whether one is on file, so the UI can show connected
    # status without the secret ever leaving the server after it is saved. Not a real column;
    # set explicitly by the endpoint after model_validate() since it isn't on the ORM object.
    mail_connected: bool = False
    # Which shipment modes this user is scoped to - required for gk2, optional (tab-filter
    # only) for operator, meaningless for every other role including manager.
    modes: list[str] | None = None
    # Set only when this account was created under a Tenant Admin's own custom type (e.g.
    # "Supervisor") instead of plain Gate Keeper 1/2/Manager. `role` above still carries the
    # type's underlying base_role — these two are purely the label to show for it.
    user_type_id: str | None = None
    user_type_name: str | None = None


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    full_name: str
    role: str
    tenant_id: str | None = None
    # A Tenant Admin's own custom type (e.g. "Supervisor") to create this account under.
    # When set, the account's actual role is forced to that type's base_role regardless of
    # what `role` above says — validated server-side against the type, not trusted from here.
    user_type_id: str | None = None
    # Template sets to assign to this user (empty = access all of their tenant's).
    template_ids: list[str] = []
    # Optional own mailbox to poll for this operator's documents. All three travel together:
    # given one, all three are required — checked live (an IMAP login) before saving.
    mail_provider: str | None = None
    mail_email: EmailStr | None = None
    mail_app_password: str | None = None
    # Required for role="gk2" (which shipment modes this user reviews); optional for
    # role="operator" (tab-filter only); rejected for every other role.
    modes: list[str] | None = None

    @field_validator("role")
    @classmethod
    def role_must_be_valid(cls, value: str) -> str:
        if value not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        return value

    @field_validator("password")
    @classmethod
    def password_min_length(cls, value: str) -> str:
        if len(value) < 8:
            raise ValueError("password must be at least 8 characters")
        return value

    @field_validator("mail_provider")
    @classmethod
    def mail_provider_must_be_valid(cls, value: str | None) -> str | None:
        if value is not None and value not in MAIL_PROVIDERS:
            raise ValueError(f"mail_provider must be one of {MAIL_PROVIDERS}")
        return value

    @field_validator("modes")
    @classmethod
    def modes_must_be_valid(cls, value: list[str] | None) -> list[str] | None:
        if value:
            bad = [m for m in value if m not in MODES]
            if bad:
                raise ValueError(f"Not a valid mode: {', '.join(bad)}")
        return value


class UserUpdate(BaseModel):
    # All optional — only provided fields are changed.
    email: EmailStr | None = None
    full_name: str | None = None
    password: str | None = None
    is_active: bool | None = None
    template_ids: list[str] | None = None  # None = leave as-is; list = replace assignments
    mail_provider: str | None = None
    mail_email: EmailStr | None = None
    # Leave unset to keep the current password; empty string clears the mailbox connection
    # entirely (provider/email cleared with it), a fresh value re-validates and replaces it.
    mail_app_password: str | None = None
    # None = leave as-is; a list (possibly empty) replaces a gk2/operator user's assigned
    # modes. Rejected (non-empty) for manager.
    modes: list[str] | None = None

    @field_validator("password")
    @classmethod
    def password_min_length_optional(cls, value: str | None) -> str | None:
        if value is not None and len(value) < 8:
            raise ValueError("password must be at least 8 characters")
        return value

    @field_validator("mail_provider")
    @classmethod
    def mail_provider_must_be_valid_optional(cls, value: str | None) -> str | None:
        if value is not None and value not in MAIL_PROVIDERS:
            raise ValueError(f"mail_provider must be one of {MAIL_PROVIDERS}")
        return value

    @field_validator("modes")
    @classmethod
    def modes_must_be_valid_optional(cls, value: list[str] | None) -> list[str] | None:
        if value:
            bad = [m for m in value if m not in MODES]
            if bad:
                raise ValueError(f"Not a valid mode: {', '.join(bad)}")
        return value
