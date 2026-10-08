from datetime import date

from sqlalchemy import Boolean, Date, Float, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class SystemSetting(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One global row - not per-tenant, this is a whole-system operator switch.

    Read fresh from the database on every check (see app/core/system_settings.py),
    unlike app.core.config.Settings which is cached for the process lifetime - the whole
    point is a Super Admin can flip these from a button and have it take effect on the
    very next email pull or extraction, with no restart.
    """

    __tablename__ = "system_settings"

    # Stops the email poller (scheduled AND manual "Check mail") before it ever connects to
    # a mailbox - no IMAP connection, no OCR, no AI call wasted while this is on.
    email_pull_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Stops document field extraction (the Extract button, auto-extract after upload, and
    # the email puller's own post-classification extraction step) before any OCR/AI call.
    extraction_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # A Super Admin's own OpenAI API key, set from Settings instead of editing .env and
    # restarting - encrypted at rest (app/core/mail_crypto.py's Fernet helper, despite the
    # module name it is not mail-specific) and never returned by any endpoint once saved,
    # not even masked - a partial value is still something "anyone can take", per the
    # Super Admin who asked for this. NULL means "use the .env default".
    openai_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # A SEPARATE key from the one above: OpenAI's own "Admin API key" (Organization > Admin
    # keys on platform.openai.com), the only key type permitted to read organization-level
    # cost/usage data. The regular API key above is used to MAKE calls and has no permission
    # to read what those calls cost — a different credential for a different purpose. Same
    # encryption/write-only handling as the key above. NULL means Spend Analytics shows only
    # this app's own local counts, with no real dollar figure.
    openai_admin_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    # How many mailboxes the email poller reads at once (see email_puller._pull_inbox).
    # Defaults to 1 deliberately - safe on any server size - rather than guessing a number
    # that might overload a small one. Capped at the server's own CPU count (see
    # app/core/system_settings.max_email_poll_workers) so a Super Admin cannot set a number
    # this machine cannot actually sustain.
    email_poll_workers: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # A JSON snapshot of the poller's own last cycle (see email_puller._record_cycle_status),
    # read by GET /email/poll-status. Persisted here rather than kept in a plain module-level
    # dict because the backend now runs as several uvicorn --workers processes: only the one
    # holding the singleton lock (app/core/worker_lock.py) ever polls, so any OTHER worker's
    # in-memory copy would sit empty forever even while polling genuinely is happening.
    last_email_cycle_status: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Manually entered by a Super Admin (Spend Analytics > OpenAI Account Balance) - OpenAI
    # has no API, not even via the Admin key, that returns account credit balance or its
    # expiry (confirmed: their own developer community has open feature requests asking for
    # exactly this, unresolved). The only place this figure exists is the platform.openai.com
    # billing dashboard, so it is typed in here rather than built on an unofficial, session-
    # cookie-based endpoint that could break at any time.
    openai_balance_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    openai_balance_expiry: Mapped[date | None] = mapped_column(Date, nullable=True)

    # Which engine production document classification and field extraction use:
    # "ocr_gpt4o_mini" (default - Document AI OCR text, read by gpt-4o-mini) or
    # "gpt5_mini_vision" (the page image, read directly by gpt-5-mini, skipping OCR text for
    # the classification/extraction DECISION itself - Document AI still runs underneath for
    # page rendering and table-row-count hints). Never affects the Template Wizard's own
    # demo-extract/prompt-generation flow, which always uses OCR text + gpt-4o-mini - those
    # call sites never read this setting at all, by construction (see app/core/llm.py's
    # build_field_profile and app/api/v1/field_marks.py's demo_extract/test-extract).
    extraction_engine: Mapped[str] = mapped_column(Text, nullable=False, default="ocr_gpt4o_mini")
