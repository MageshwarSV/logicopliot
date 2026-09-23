from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.core.database import Base


class VerificationLink(Base):
    """Links a field mapping on Document A to a field mapping on Document B."""

    __tablename__ = "verification_links"

    id = Column(Integer, primary_key=True, index=True)
    source_field_mapping_id = Column(Integer, ForeignKey("field_mappings.id"), nullable=False)
    target_field_mapping_id = Column(Integer, ForeignKey("field_mappings.id"), nullable=False)
    condition = Column(String(30), nullable=False)  # must_equal | must_be_greater_than | must_contain
    created_at = Column(DateTime(timezone=True), server_default=func.now())
