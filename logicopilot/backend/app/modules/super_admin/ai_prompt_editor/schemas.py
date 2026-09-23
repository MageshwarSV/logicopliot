from datetime import datetime

from pydantic import BaseModel


class TransformationPromptBase(BaseModel):
    prompt_text: str


class TransformationPromptUpsert(TransformationPromptBase):
    pass


class TransformationPromptRead(TransformationPromptBase):
    id: int
    field_mapping_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
