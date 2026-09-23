from datetime import datetime

from pydantic import BaseModel


class TemplateMeta(BaseModel):
    """Form fields sent alongside the uploaded file (multipart/form-data)."""

    name: str
    document_type: str  # Invoice | PackingList | BL | Custom


class TemplateRead(BaseModel):
    id: int
    tenant_id: int
    name: str
    document_type: str
    file_path: str
    page_count: int
    created_at: datetime

    class Config:
        from_attributes = True
