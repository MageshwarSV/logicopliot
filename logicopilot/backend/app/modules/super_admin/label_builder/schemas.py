from datetime import datetime

from pydantic import BaseModel


class LabelBase(BaseModel):
    field_name: str
    data_type: str  # String | Number | Date | Currency
    is_required: bool = False
    verify_with_other_document: bool = False


class LabelCreate(LabelBase):
    pass


class LabelUpdate(BaseModel):
    field_name: str | None = None
    data_type: str | None = None
    is_required: bool | None = None
    verify_with_other_document: bool | None = None


class LabelRead(LabelBase):
    id: int
    tenant_id: int
    created_at: datetime

    class Config:
        from_attributes = True
