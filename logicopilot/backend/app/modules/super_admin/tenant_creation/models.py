from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.sql import func

from app.core.database import Base


class Tenant(Base):
    """A customer company onboarded by the Super Admin (e.g. '4S Logistics')."""

    __tablename__ = "tenants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False)
    primary_contact_email = Column(String(255), nullable=False)
    region = Column(String(20), nullable=False)  # US | EU | ASIA
    base_currency = Column(String(10), nullable=False)  # USD | EUR | GBP
    timezone = Column(String(50), nullable=True)
    logo_url = Column(String(500), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
