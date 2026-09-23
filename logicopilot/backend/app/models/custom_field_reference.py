from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin


class CustomFieldReferenceValue(Base, TimestampMixin):
    """What a lookup-backed field resolved to, learned the one time someone supplied it because
    the automatic answer had nothing. Two independent uses share this one table:

    - kind="lookup" custom fields (per_row only): the customer's material master had nothing
      for a line, an operator typed it in, and up to four identifying values (usually a
      material code and a description) are kept here so the SAME line never has to be typed
      twice - on this job or any later one.
    - is_target_value fields (either a FieldMark or a CustomField, not per_row-only): the
      field's own extracted value IS the key - a single slot (match_value_1), nothing derived.
      A match replaces the extraction with the stored value; no match returns empty.

    Owned by exactly ONE of custom_field_id / mark_id (see the check constraint) - the same
    part code can mean a different thing to a different field, so a row only ever answers for
    the one field it was learned against. Matching is exact on normalised text, never fuzzy -
    the same rule material_master.py uses for the sheet the lookup case backs up, because a
    near-miss here is a wrong customs declaration, not a near-enough guess."""

    __tablename__ = "custom_field_reference_values"
    __table_args__ = (
        UniqueConstraint(
            "custom_field_id", "mark_id", "match_value_1", "match_value_2", "match_value_3", "match_value_4",
            name="uq_custom_field_reference_values_key",
        ),
        CheckConstraint(
            "(custom_field_id IS NOT NULL AND mark_id IS NULL) OR "
            "(custom_field_id IS NULL AND mark_id IS NOT NULL)",
            name="ck_custom_field_reference_values_one_owner",
        ),
    )

    # A running row number, not a UUID: this table is meant to be looked at and edited directly
    # (the admin screen, the downloaded template) where a short number reads far better than a
    # 36-character id, and nothing else ever references a row of it by id.
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    custom_field_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("custom_fields.id", ondelete="CASCADE"), nullable=True, index=True
    )
    mark_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("field_marks.id", ondelete="CASCADE"), nullable=True, index=True
    )

    match_value_1: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    match_value_2: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    match_value_3: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    match_value_4: Mapped[str] = mapped_column(String(255), nullable=False, default="")

    resolved_value: Mapped[str] = mapped_column(Text, nullable=False)
