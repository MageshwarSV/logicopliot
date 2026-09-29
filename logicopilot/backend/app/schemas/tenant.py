from pydantic import BaseModel, ConfigDict


from pydantic import EmailStr


class TenantCreate(BaseModel):
    name: str
    region: str | None = None
    currency: str | None = None
    # Which transport modes (app/core/modes.py) this client is licensed for. Empty/omitted =
    # unrestricted — every template creation offers all of them, same as before this existed.
    allowed_modes: list[str] | None = None
    # Optional: create the tenant's admin login in the same step.
    admin_full_name: str | None = None
    admin_email: EmailStr | None = None
    admin_password: str | None = None


class TenantUpdate(BaseModel):
    # A Tenant Admin calls this from their own "Shipment Type" page to manage which modes
    # THEIR tenant is licensed for; a Super Admin can also use it for the same tenant-level
    # field. Empty list = unrestricted, same meaning as at creation.
    allowed_modes: list[str] | None = None
    # Set from the "Masters" page: {"operator": bool, "gk2": bool} — False locks that role's
    # users in this tenant to read-only. A key not being present leaves that role's current
    # setting unchanged (a bare {} or omitting the field entirely is a no-op, not a reset).
    role_write_enabled: dict[str, bool] | None = None


class TenantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    region: str | None
    currency: str | None
    is_active: bool
    allowed_modes: list[str] | None = None
    role_write_enabled: dict[str, bool] | None = None
    irn_pending_access_key: str | None = None
