from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


class TenantBase(BaseModel):
    name: str
    primary_contact_email: EmailStr
    region: str  # US | EU | ASIA
    base_currency: str  # USD | EUR | GBP
    timezone: Optional[str] = None
    logo_url: Optional[str] = None


class TenantCreate(TenantBase):
    pass


class TenantUpdate(BaseModel):
    name: Optional[str] = None
    primary_contact_email: Optional[EmailStr] = None
    region: Optional[str] = None
    base_currency: Optional[str] = None
    timezone: Optional[str] = None
    logo_url: Optional[str] = None


class TenantRead(TenantBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
