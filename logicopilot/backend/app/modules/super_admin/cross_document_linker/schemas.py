from datetime import datetime

from pydantic import BaseModel


class VerificationLinkBase(BaseModel):
    source_field_mapping_id: int
    target_field_mapping_id: int
    condition: str  # must_equal | must_be_greater_than | must_contain


class VerificationLinkCreate(VerificationLinkBase):
    pass


class VerificationLinkRead(VerificationLinkBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True
