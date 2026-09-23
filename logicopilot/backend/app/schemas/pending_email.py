from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PendingAttachmentOut(BaseModel):
    name: str
    ext: str
    # The on-disk filename — what GET /pending-emails/{id}/attachments/{path} expects.
    # NOT the same as `name`: two attachments can share a display name ("Invoice.pdf" twice
    # on one mail), but each has its own unique, randomised path on disk.
    path: str


class PendingEmailOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    message_id: str
    sender: str | None = None
    subject: str | None = None
    reason: str | None = None
    evidence: str | None = None
    status: str
    resolved_group_id: str | None = None
    resolved_job_id: str | None = None
    created_at: datetime
    attachments: list[PendingAttachmentOut] = []

    @staticmethod
    def from_row(row) -> "PendingEmailOut":
        atts = [
            PendingAttachmentOut(name=a.get("name", ""), ext=a.get("ext", ""),
                                 path=a.get("path", ""))
            for a in (row.attachments or [])
        ]
        out = PendingEmailOut.model_validate(row)
        out.attachments = atts
        return out


class PendingEmailResolve(BaseModel):
    group_id: str
