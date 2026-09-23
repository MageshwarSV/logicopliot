from sqlalchemy import Column, Integer, String, Float, Text, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.core.database import Base


class FieldMapping(Base):
    """A bounding box on a template page, linked to a Label."""

    __tablename__ = "field_mappings"

    id = Column(Integer, primary_key=True, index=True)
    template_id = Column(Integer, ForeignKey("templates.id"), nullable=False, index=True)
    label_id = Column(Integer, ForeignKey("labels.id"), nullable=False, index=True)
    page_number = Column(Integer, default=1, nullable=False)
    # Normalized 0-1 coordinates so boxes scale to any image resolution.
    x = Column(Float, nullable=False)
    y = Column(Float, nullable=False)
    width = Column(Float, nullable=False)
    height = Column(Float, nullable=False)
    color = Column(String(20), nullable=False)  # red | green | blue | yellow | purple
    anchor_term = Column(String(255), nullable=True)  # auto-detected surrounding text
    generated_prompt = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
