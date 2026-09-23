from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.core.database import Base


class Label(Base):
    """A custom data tag defined by the Super Admin (e.g. `invoice_no`)."""

    __tablename__ = "labels"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    field_name = Column(String(100), nullable=False)  # e.g. invoice_no, gross_weight
    data_type = Column(String(20), nullable=False)  # String | Number | Date | Currency
    is_required = Column(Boolean, default=False, nullable=False)
    verify_with_other_document = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
