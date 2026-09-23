from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.core.database import Base


class Template(Base):
    """A sample reference document uploaded to teach the system a layout."""

    __tablename__ = "templates"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(Integer, ForeignKey("tenants.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)  # e.g. "Maersk Bill of Lading v2"
    document_type = Column(String(20), nullable=False)  # Invoice | PackingList | BL | Custom
    file_path = Column(String(500), nullable=False)
    page_count = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
