from sqlalchemy import Boolean, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class ConnectedMailbox(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A mailbox connected for the email puller to read - independent of any user.

    Connecting one needs nothing but its own credentials (see app/api/v1/
    email_puller_mailboxes.py): no tenant, no template. tenant_id/group_id are set
    afterwards, separately, whenever a Super Admin is ready - null on either means
    "Unassigned", and the puller (see app/core/email_puller.py) skips a mailbox with no
    tenant_id entirely, since there is nowhere to route its mail into yet.

    Replaces the OLD approach of putting mail_provider/mail_email/mail_app_password_encrypted
    directly on an operator's own User row (those columns still exist there, untouched, for
    the data migration that carried every already-working one of them into this table - see
    that migration's own docstring - but nothing NEW is ever written to them after this).
    """

    __tablename__ = "connected_mailboxes"

    provider: Mapped[str] = mapped_column(String(20), nullable=False)  # "gmail" | "zoho"
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True, index=True)
    # The exact host a live IMAP login actually succeeded on (matters for Zoho's regional
    # data centers - see _verify_mailbox's own docstring in users.py, which this reuses).
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    app_password_encrypted: Mapped[str] = mapped_column(Text, nullable=False)

    # null = Unassigned. Set independently of group_id - a mailbox can be tenant-assigned
    # with no specific template chosen yet, in which case the puller still identifies the
    # customer from the documents themselves across every template in this tenant.
    tenant_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Set only once a specific template is picked - routes every message from this mailbox
    # directly into this one template, no document-based identification needed at all.
    group_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("template_groups.id", ondelete="SET NULL"), nullable=True, index=True
    )

    paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
