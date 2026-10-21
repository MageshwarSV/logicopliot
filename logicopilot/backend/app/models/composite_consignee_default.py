from sqlalchemy import ForeignKey, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CompositeFieldConsigneeDefault(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Remembers the full Combined Description FORMAT (field order, fixed text, and each
    piece's own separator) a composite field was last configured with FOR ONE CONSIGNEE - so
    the next job for the SAME consignee pre-fills, and computes, with that exact format,
    instead of falling back to whatever generic default the template happens to have.

    Built for the Combined Description Configuration screen (saved directly against a
    consignee, independent of any one job) - the latest save always replaces whatever this
    consignee already had, with no confirmation step: this is a deliberate admin action, not an
    incidental edit made while working a job.

    source_labels now stores the FULL pieces list, fixed-value pieces included - unlike this
    table's earlier, narrower design (which deliberately dropped them, on the theory a literal
    typed in for one job was not assumed to be the same text next time). A format's own fixed
    text (a bracket, a dash) IS part of what gets configured and reused here now, by design.
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
    # The full ordered pieces list - field-reference label_name strings AND {"fixed": "<text>"}
    # literals alike (see class docstring).
    source_labels: Mapped[list] = mapped_column(JSON, nullable=False)
    # Parallel to source_labels, same length/order - separators[i] is the text inserted
    # immediately before piece i. None (a row saved before this existed) falls back to a single
    # space before every piece - see _join_composite_pieces in app/api/v1/jobs.py.
    separators: Mapped[list | None] = mapped_column(JSON, nullable=True)
