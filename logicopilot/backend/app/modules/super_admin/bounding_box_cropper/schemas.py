from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class FieldMappingBase(BaseModel):
    template_id: int
    label_id: int
    page_number: int = 1
    x: float
    y: float
    width: float
    height: float
    color: str  # red | green | blue | yellow | purple


class FieldMappingCreate(FieldMappingBase):
    pass


class FieldMappingRead(FieldMappingBase):
    id: int
    anchor_term: Optional[str] = None
    generated_prompt: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True
