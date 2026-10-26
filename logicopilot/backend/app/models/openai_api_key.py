from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class OpenAIApiKey(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One of a POOL of OpenAI API keys a Super Admin can add. tenant_id NULL (the only thing
    every key saved before this column existed has, and still the default) means this key is
    in the global pool, shared like SystemSetting's own single row - one shared OpenAI
    account's call capacity. tenant_id set means this key belongs to exactly that tenant's own
    pool (see TenantExtractionConfig.openai_key_mode == "own") - a tenant with one such key has
    a single dedicated key, a tenant with several has a real pool of its own, same mechanism.

    Each key should normally come from a SEPARATE OpenAI Project (Settings -> Projects ->
    API keys on platform.openai.com): OpenAI meters rate limits (RPM/TPM) per project, so
    several keys from the SAME project still share one budget and spreading calls across
    them would not raise the ceiling at all - only keys from different projects each bring
    their own separate budget, which app/core/openai_pool.py's round-robin genuinely adds
    together.

    Encrypted at rest exactly like SystemSetting.openai_api_key_encrypted (same Fernet
    helper, app/core/mail_crypto.py) and never returned by any endpoint once saved - same
    write-only principle as every other secret in this codebase.
    """

    __tablename__ = "openai_api_keys"

    # NULL = global pool (every row before this column existed, and still the default for a
    # new key added from the global Settings page). Set = this one tenant's own pool/key -
    # added from that tenant's own section instead.
    tenant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True
    )
    # What a Super Admin calls it in the UI (e.g. "Project A", "Backup key") - purely a
    # label, never used to pick a key at request time.
    label: Mapped[str] = mapped_column(Text, nullable=False)
    key_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Lets a Super Admin take a key out of rotation (hit its billing limit, being rotated
    # out) without losing its label/history the way deleting it would.
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Display order only - round-robin position is independent of this, it only decides
    # how rows are LISTED so the UI stays predictable across reloads.
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
