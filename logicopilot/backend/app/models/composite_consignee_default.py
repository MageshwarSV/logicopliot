from sqlalchemy import ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CompositeFieldConsigneeDefault(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Remembers the field order a composite field (e.g. "Combined Description") was last set
    to FOR ONE CONSIGNEE - so the next job for the SAME consignee pre-fills, and computes,
    with the order that consignee's own jobs already use, instead of falling back to whatever
    generic default the template happens to have.

    Deliberately never stores a fixed-value piece (see CustomField.composite_source_labels's
    own "fixed" pieces) - a literal typed in for one job is not assumed to be the same text
    next time, only the ORDER of real fields is remembered here.
    """

    __tablename__ = "composite_field_consignee_defaults"
    __table_args__ = (
        UniqueConstraint("custom_field_id", "consignee_key", name="uq_composite_consignee_default"),
    )

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False, index=True
    )
    custom_field_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("custom_fields.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The consignee's own name/value, exactly as this job's own consignee_full_name field
    # reads it (stripped of surrounding whitespace only - no fuzzy matching here, unlike
    # CustomField.fuzzy_match elsewhere: two genuinely different-but-similar consignee names
    # silently sharing one remembered order would be a worse mistake than an exact-text
    # consignee occasionally missing its own history).
    consignee_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Field-reference label_names only, in order - never a {"fixed": ...} piece (see class
    # docstring).
    source_labels: Mapped[list] = mapped_column(JSON, nullable=False)
