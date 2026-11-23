"""A folded subject / attachment name (CR/LF inside) must not stop a pre-alert split (2026-11-22)."""
import email
import email.policy
from email.message import EmailMessage

from app.core.prealert_shipments import split_message


def test_split_survives_line_breaks_in_subject_and_filename():
    msg = EmailMessage()
    msg["From"] = "a@x.com"
    msg["To"] = "b@y.com"
    msg["Message-ID"] = "<m1@x>"
    msg.set_content("body")
    subject = "Fwd: PRE ALERT HAWB# 9AN1 _NETAPP INDIA\r\n MARKETING //9AN1 //9AN2"
    files = [("DGF_(S)NETAPP IRELAND\r\n UNLIMITED.pdf", b"%PDF-1"), ("9AN2.pdf", b"%PDF-2")]
    shipments = [{"hawb": "9AN1"}, {"hawb": "9AN2"}]
    out = split_message(msg, subject, shipments, files, {"9AN1": [0], "9AN2": [1]})
    assert len(out) == 2
    first = email.message_from_bytes(out[0], policy=email.policy.default)
    names = [p.get_filename() for p in first.iter_attachments()]
    assert names == ["DGF_(S)NETAPP IRELAND UNLIMITED.pdf"]
    assert "\n" not in first["Subject"] and first["Subject"].endswith("//9AN1")
