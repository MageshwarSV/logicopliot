from sqlalchemy import Column, Integer, Text, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.core.database import Base


class TransformationPrompt(Base):
    """Natural-language formatting instruction attached to a single field mapping."""

    __tablename__ = "transformation_prompts"

    id = Column(Integer, primary_key=True, index=True)
    field_mapping_id = Column(Integer, ForeignKey("field_mappings.id"), nullable=False, unique=True, index=True)
    prompt_text = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
