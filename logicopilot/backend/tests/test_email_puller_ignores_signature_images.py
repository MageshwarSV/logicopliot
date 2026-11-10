"""A footer logo is part of the letter, not a document somebody sent you.

Reported on 4S Logistics' own mail: every message from them carries a company logo plus AEO,
FTWZ, MSME and anniversary badges in the signature. Those arrived as .png attachments, were
OCR'd, classified by the AI, and landed on the job as pages nobody had sent - costing a full
classification pass each and showing up under "Not required pages".

_extract_attachments already skipped Content-Disposition: inline. That is only one of the ways
a client embeds an image, and not the common one: Outlook and Zoho hang the logo off a
Content-ID and reference it from the HTML as <img src="cid:...">, with either no disposition at
all or - Outlook - "attachment". The inline check never saw those.

The case these tests most exist to protect is the opposite one. Customers really do send a
photograph of a document, and a .jpg with no Content-ID is exactly that. Losing a customer's
invoice would be far worse than carrying a logo, so every rule here is deliberately
conservative: a part is only dropped when it is clearly part of the letter.
"""
import email
from email.message import EmailMessage

from app.core.email_puller import _extract_attachments


def _names(msg):
    return [name for name, _ in _extract_attachments(msg)]


# ---- what must be KEPT ---------------------------------------------------------------------

def test_a_real_pdf_attachment_is_kept():
    msg = EmailMessage()
    msg.set_content("Please see attached.")
    msg.add_attachment(b"%PDF-fake", maintype="application", subtype="pdf",
                       filename="invoice.pdf")
    assert _names(msg) == ["invoice.pdf"]


def test_a_photographed_document_is_kept():
    """A .jpg with no Content-ID is a customer photographing their paperwork - the single
    most important thing these rules must not throw away."""
    msg = EmailMessage()
    msg.set_content("Photo of the BL attached.")
    msg.add_attachment(b"\xff\xd8\xff-fake-jpeg", maintype="image", subtype="jpeg",
                       filename="bill_of_lading.jpg")
    assert _names(msg) == ["bill_of_lading.jpg"]


def test_a_real_attachment_that_also_carries_a_content_id_is_kept():
    """Some clients give every part a Content-ID. When it also says "attachment" and the body
    never displays it, it is a document."""
    raw = (
        b"Content-Type: multipart/mixed; boundary=B\r\n\r\n"
        b"--B\r\nContent-Type: text/html\r\n\r\n<p>Docs attached.</p>\r\n"
        b"--B\r\nContent-Type: application/pdf\r\n"
        b'Content-Disposition: attachment; filename="packing_list.pdf"\r\n'
        b"Content-ID: <part9@mail>\r\n\r\n%PDF-fake\r\n"
        b"--B--\r\n"
    )
    assert _names(email.message_from_bytes(raw)) == ["packing_list.pdf"]


# ---- what must be DROPPED ------------------------------------------------------------------

def test_an_inline_signature_image_is_dropped():
    msg = EmailMessage()
    msg.set_content("Regards,\nJohn")
    msg.add_attachment(b"\x89PNG-logo", maintype="image", subtype="png",
                       filename="logo.png", disposition="inline")
    assert _names(msg) == []


def test_a_logo_the_html_body_displays_is_dropped_even_marked_attachment():
    """Outlook's habit: Content-Disposition: attachment on an image the body is showing."""
    raw = (
        b"Content-Type: multipart/related; boundary=B\r\n\r\n"
        b"--B\r\nContent-Type: text/html\r\n\r\n"
        b'<p>Thanks</p><img src="cid:logo001">\r\n'
        b"--B\r\nContent-Type: image/png\r\n"
        b'Content-Disposition: attachment; filename="4s_logo.png"\r\n'
        b"Content-ID: <logo001>\r\n\r\n\x89PNG-fake\r\n"
        b"--B--\r\n"
    )
    assert _names(email.message_from_bytes(raw)) == []


def test_a_logo_with_a_content_id_and_no_disposition_is_dropped():
    """A Content-ID exists in order to be referenced from the body. Zoho sends them with no
    disposition header at all."""
    raw = (
        b"Content-Type: multipart/related; boundary=B\r\n\r\n"
        b"--B\r\nContent-Type: text/html\r\n\r\n<p>Regards</p>\r\n"
        b"--B\r\nContent-Type: image/png; name=\"aeo_badge.png\"\r\n"
        b"Content-ID: <badge77>\r\n\r\n\x89PNG-fake\r\n"
        b"--B--\r\n"
    )
    assert _names(email.message_from_bytes(raw)) == []


def test_a_whole_signature_block_is_dropped_but_the_invoice_survives():
    """The real shape of a 4S Logistics mail: one genuine PDF, four footer graphics."""
    parts = [
        b"Content-Type: multipart/mixed; boundary=B\r\n\r\n",
        b"--B\r\nContent-Type: text/html\r\n\r\n"
        b'<p>HBL: 897140033682</p><img src="cid:c1"><img src="cid:c2">'
        b'<img src="cid:c3"><img src="cid:c4">\r\n',
        b"--B\r\nContent-Type: application/pdf\r\n"
        b'Content-Disposition: attachment; filename="897140033682 INVOICE.pdf"\r\n\r\n'
        b"%PDF-real\r\n",
    ]
    for i, name in enumerate(("logo.png", "aeo.png", "ftwz.png", "anniversary.png"), start=1):
        parts.append(
            b"--B\r\nContent-Type: image/png\r\n"
            b'Content-Disposition: inline; filename="' + name.encode() + b'"\r\n'
            b"Content-ID: <c" + str(i).encode() + b">\r\n\r\n\x89PNG\r\n"
        )
    parts.append(b"--B--\r\n")
    assert _names(email.message_from_bytes(b"".join(parts))) == ["897140033682 INVOICE.pdf"]


def test_an_email_of_nothing_but_a_signature_yields_no_attachments():
    """This is what lets such a mail take the cheap "no documents" path instead of paying for
    an OCR and AI classification pass on a logo."""
    raw = (
        b"Content-Type: multipart/related; boundary=B\r\n\r\n"
        b"--B\r\nContent-Type: text/html\r\n\r\n<p>Noted, thanks.</p><img src=\"cid:z1\">\r\n"
        b"--B\r\nContent-Type: image/jpeg\r\n"
        b'Content-Disposition: inline; filename="sig.jpg"\r\n'
        b"Content-ID: <z1>\r\n\r\n\xff\xd8\xff\r\n"
        b"--B--\r\n"
    )
    assert _names(email.message_from_bytes(raw)) == []
