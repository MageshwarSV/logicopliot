"""A supplier that puts two invoices in ONE PDF must get two documents by EMAIL too.

Smart Upload has split combined multi-invoice files since 2026-11-02 (20aafae). That fix went
into `_classify_and_place_files`, which only the upload and reclassify endpoints call - email
has its own placement code in email_puller.py and never got it. So the same customer's mail
produced one Invoice document holding two invoices, while uploading the identical file by hand
produced two. Found on JOB-1130B4 (SIT) and JOB-CB9B46 (production): a 4Slogistics supplier
sends [invoice 8667395, terms, invoice 8667396, terms] as one attachment.

The classifier cannot tell them apart up front - every page equally looks like "an Invoice
page" - so the split has to happen on the invoice number printed on the pages themselves.
"""
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import fitz

from app.core.email_puller import _pull_one_mailbox
from app.models.job import JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant, make_user

# What the two invoices actually print, in the order the pages arrive.
PAGE_TEXTS = [
    "COMMERCIAL INVOICE\nAdvanced Energy Industries\nInvoice No: 8667395\nShip to: Chennai",
    "Terms and Conditions\nAll sales are subject to the seller's standard terms.",
    "COMMERCIAL INVOICE\nAdvanced Energy Industries\nInvoice No: 8667396\nShip to: Chennai",
    "Terms and Conditions\nAll sales are subject to the seller's standard terms.",
]


def _pdf_bytes(pages: int) -> bytes:
    doc = fitz.open()
    for _ in range(pages):
        doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def _message(message_id: str, pages: int, filename: str) -> bytes:
    msg = EmailMessage()
    msg["Message-ID"] = message_id
    msg["From"] = "supplier@example.com"
    msg["Subject"] = "Shipment docs"
    msg.set_content("Please find attached.")
    msg.add_attachment(_pdf_bytes(pages), maintype="application", subtype="pdf",
                       filename=filename)
    return msg.as_bytes()


def _conn(raw: bytes):
    conn = MagicMock()
    conn.search.return_value = ("OK", [b"1"])
    conn.fetch.return_value = ("OK", [(b"1 (BODY.PEEK[] {n}", raw)])
    return conn


def _setup(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="ready")
    db_session.add(group)
    db_session.flush()
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice",
                           doc_type="Invoice", order_index=0)
    db_session.add(doc)
    db_session.commit()
    return group, doc


def _pull(db_session, tenant, group, raw, page_texts, claims):
    with patch("app.core.email_puller._connect", return_value=_conn(raw)), \
         patch("app.core.docai.ocr_page_image",
               side_effect=[{"text": t, "tokens": []} for t in page_texts]), \
         patch("app.core.classifier_sea_import.assign_documents_detailed",
               return_value=[claims]):
        return _pull_one_mailbox(db_session, tenant_id=tenant.id, forced_group_id=group.id,
                                 mail_email="op@zoho.com", mail_app_password="x",
                                 mail_host="imap.zoho.com")


def _invoice_docs(db_session, doc_id):
    return (db_session.query(JobDocument)
            .filter(JobDocument.template_document_id == doc_id,
                    JobDocument.file_path.isnot(None))
            .order_by(JobDocument.file_index).all())


def test_two_invoices_in_one_mailed_pdf_become_two_documents(db_session):
    """The whole point. One attachment, one Invoice claim covering all four pages, two
    invoice numbers printed on them - two documents, not one holding both."""
    tenant = make_tenant(db_session)
    make_user(db_session, role="operator", tenant=tenant, email="split-op@example.com")
    group, doc = _setup(db_session, tenant)

    _pull(db_session, tenant, group, _message("<split-1@x>", 4, "Commercial Invoice 380.PDF"),
          PAGE_TEXTS, [{"key": doc.id, "pages": [1, 2, 3, 4], "evidence": "commercial invoice"}])

    rows = _invoice_docs(db_session, doc.id)
    assert len(rows) == 2, (
        "a mailed PDF holding two invoices must land as two documents - got "
        f"{len(rows)} with page counts {[r.page_count for r in rows]}"
    )
    assert sorted(r.page_count for r in rows) == [2, 2], "each invoice keeps its own two pages"


def test_the_two_documents_are_separate_rows_over_separate_files(db_session):
    """Two rows is not enough on its own: each has to be its own file on disk holding its own
    pages. A second row pointing at the same 4-page file would read as "split" in the database
    and still show the operator both invoices in each document."""
    tenant = make_tenant(db_session)
    make_user(db_session, role="operator", tenant=tenant, email="split-op2@example.com")
    group, doc = _setup(db_session, tenant)

    _pull(db_session, tenant, group, _message("<split-2@x>", 4, "Commercial Invoice 380.PDF"),
          PAGE_TEXTS, [{"key": doc.id, "pages": [1, 2, 3, 4], "evidence": "commercial invoice"}])

    rows = _invoice_docs(db_session, doc.id)
    assert [r.file_index for r in rows] == [0, 1], "two rows, numbered in order"
    assert len({r.file_path for r in rows}) == 2, "each document needs its own file on disk"
    for row in rows:
        with fitz.open(row.file_path) as pdf:
            assert pdf.page_count == 2, (
                f"{row.file_path} holds {pdf.page_count} pages - a document carrying all four "
                "is the whole bug, whatever the row count says"
            )


def test_a_single_invoice_file_is_untouched(db_session):
    """The ordinary case must not change: one invoice in one file stays one document, whole."""
    tenant = make_tenant(db_session)
    make_user(db_session, role="operator", tenant=tenant, email="split-op3@example.com")
    group, doc = _setup(db_session, tenant)

    _pull(db_session, tenant, group, _message("<split-3@x>", 2, "Invoice.PDF"),
          ["COMMERCIAL INVOICE\nInvoice No: 8667395\npage one",
           "Invoice No: 8667395\ncontinuation page"],
          [{"key": doc.id, "pages": [1, 2], "evidence": "commercial invoice"}])

    rows = _invoice_docs(db_session, doc.id)
    assert len(rows) == 1, "one invoice must stay one document"
    assert rows[0].page_count == 2, "and keep both of its pages"


def test_pages_with_no_readable_number_do_not_split(db_session):
    """A page the OCR could not read must never become a phantom extra invoice - it stays
    attached to whichever invoice came before it."""
    tenant = make_tenant(db_session)
    make_user(db_session, role="operator", tenant=tenant, email="split-op4@example.com")
    group, doc = _setup(db_session, tenant)

    _pull(db_session, tenant, group, _message("<split-4@x>", 3, "Invoice.PDF"),
          ["COMMERCIAL INVOICE\nInvoice No: 8667395", "...", "....."],
          [{"key": doc.id, "pages": [1, 2, 3], "evidence": "commercial invoice"}])

    rows = _invoice_docs(db_session, doc.id)
    assert len(rows) == 1, "unreadable pages must not invent extra invoices"
    assert rows[0].page_count == 3
