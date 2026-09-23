from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class TrainingCorrectionBase(BaseModel):
    original_value: Optional[str] = None
    corrected_value: str


class TrainingCorrectionCreate(TrainingCorrectionBase):
    pass


class TrainingCorrectionRead(TrainingCorrectionBase):
    id: int
    field_mapping_id: int
    applied_as_few_shot: bool
    created_at: datetime

    class Config:
        from_attributes = True
