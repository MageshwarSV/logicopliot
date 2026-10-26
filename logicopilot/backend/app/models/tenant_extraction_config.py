from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin


class TenantExtractionConfig(Base, TimestampMixin):
    """Per-tenant AI-processing controls a Super Admin can set independently of the platform-
    wide ones (SystemSetting.extraction_paused, the global OpenAI key pool) - one tenant's
    needs (pause just their own extraction, give them their own OpenAI key/pool) no longer
    require touching every other tenant. One row per tenant, created on first save rather
    than at tenant creation - a tenant with no row yet simply behaves as today (not paused,
    shared pool), not an error.

    extraction_paused here is checked IN ADDITION TO the existing global
    SystemSetting.extraction_paused (see is_extraction_paused in system_settings.py) - the
    global switch still works as a platform-wide emergency stop; this adds a second,
    independent per-tenant one on top of it, rather than replacing it.
    """

    __tablename__ = "tenant_extraction_configs"

    tenant_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="CASCADE"), primary_key=True
    )

    extraction_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # "shared" = this tenant's AI calls use the platform's global OpenAI key pool, exactly as
    # every tenant does today. "own" = use ONLY this tenant's own OpenAIApiKey rows
    # (OpenAIApiKey.tenant_id == this tenant) - a single key or a real pool of several is the
    # SAME mechanism here, just how many rows this tenant happens to have added.
    openai_key_mode: Mapped[str] = mapped_column(String(10), nullable=False, default="shared")
