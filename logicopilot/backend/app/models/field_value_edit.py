from sqlalchemy import Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class FieldValueEdit(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One operator edit of one extracted field: the value before, the value after, and what
    the AI had read. Written by app.core.field_edits.record at the moment the edit is saved.

    It feeds Super Admin -> Extraction Corrections: which fields are corrected on many
    different jobs (a recurring extraction error worth a better prompt) and how often.
    JobFieldValue keeps only the LATEST corrected value, so without this every edit after the
    first is lost.

    Stands on its own, like the billing ledger and JobDeletion: job_id is a plain string and
    the job reference, field name and editor are copied in, so the record still reads
    correctly after the job, the field or the user is deleted or renamed."""

    __tablename__ = "field_value_edits"

    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    job_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    job_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    group_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    template_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    job_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    mark_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    custom_field_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    field_value_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    label_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    row_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    set_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ai_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    before_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "edit" - one field saved on the job page; "carry" - one value carried across lines.
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="edit")
    edited_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    edited_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
