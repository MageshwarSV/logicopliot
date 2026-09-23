from sqlalchemy import Column, Integer, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func

from app.core.database import Base


class TrainingCorrection(Base):
    """A manual correction submitted via 'Train Data', stored as a few-shot example."""

    __tablename__ = "training_corrections"

    id = Column(Integer, primary_key=True, index=True)
    field_mapping_id = Column(Integer, ForeignKey("field_mappings.id"), nullable=False, index=True)
    original_value = Column(Text, nullable=True)
    corrected_value = Column(Text, nullable=False)
    applied_as_few_shot = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
