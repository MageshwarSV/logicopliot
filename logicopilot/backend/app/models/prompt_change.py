from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class PromptChange(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One change to a field's extraction prompt (and format hint) made from Super Admin ->
    Extraction Corrections -> Fix: the values before and after, who, and why. Kept so any
    change can be seen and reverted - a revert is itself a new row.

    Owned by exactly one of mark_id (a field read off a document) / custom_field_id (a
    computed AI field). No foreign keys: the history must outlive a deleted field."""

    __tablename__ = "prompt_changes"

    tenant_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    group_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    mark_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    custom_field_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    label_name: Mapped[str] = mapped_column(String(100), nullable=False)
    old_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    old_format_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    new_format_hint: Mapped[str | None] = mapped_column(Text, nullable=True)
    # "apply" - Apply Changes on the Fix screen; "revert" - Revert of an earlier change.
    action: Mapped[str] = mapped_column(String(20), nullable=False, default="apply")
    changed_by_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    changed_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
