"""BillablePageEvent: a durable, append-only ledger of pages actually processed, written at
every _render_pages() call site so a later delete (of a single page, or the whole job) can
never erase AI-processing cost that was already incurred. See app/core/billing.py's
record_billable_pages/compute_usage and app/models/billable_page_event.py's own docstring for
the full reasoning - JobDocument.page_count alone is not billing-safe, since deleting a page
zeroes/removes its row and jobs can be hard-deleted entirely (an operator's own DELETE
/jobs/{id}, and the automatic old-job sweep in custom_filter_pages.py)."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import fitz

from app.core.billing import compute_usage
from app.models.billable_page_event import BillablePageEvent
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _pdf_bytes(n_pages: int = 1) -> bytes:
    doc = fitz.open()
    for i in range(n_pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"page {i + 1}")
    data = doc.tobytes()
    doc.close()
    return data


def _make_template(db_session, tenant, mode="Sea Import"):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode=mode)
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="Invoice No",
                     page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(mark)
    other_tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List",
                                  doc_type="packing_list", is_required=True)
    db_session.add(other_tdoc)
    db_session.commit()
    return group, tdoc


def _make_job(db_session, tenant, group, tdoc):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-LEDGER", status="draft")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    slot = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                       file_path=None, page_count=0, file_index=0)
    db_session.add(slot)
    db_session.commit()
    return job


def test_delete_and_reupload_both_get_billed_even_though_only_one_jobdocument_row_remains(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-ledger1@example.com")
    login(client, op.email)

    first = client.post(
        f"/api/v1/jobs/{job.id}/documents/{tdoc.id}/upload",
        files={"file": ("invoice.pdf", _pdf_bytes(2), "application/pdf")},
    )
    assert first.status_code == 200, first.text
    before = next(d for d in first.json()["documents"] if d["template_document_id"] == tdoc.id)

    resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{before['id']}/file")
    assert resp.status_code == 200, resp.text
    # The delete zeroes the JobDocument's own page_count - confirming the bug this ledger
    # exists to work around.
    jd = db_session.get(JobDocument, before["id"])
    assert jd.page_count == 0

    second = client.post(
        f"/api/v1/jobs/{job.id}/documents/{tdoc.id}/upload",
        files={"file": ("invoice-corrected.pdf", _pdf_bytes(3), "application/pdf")},
    )
    assert second.status_code == 200, second.text

    # Both uploads are permanently on record - 2 pages from the first, 3 from the second -
    # even though the live JobDocument row only reflects the second (3).
    events = db_session.query(BillablePageEvent).filter(BillablePageEvent.job_id == job.id).order_by(
        BillablePageEvent.created_at
    ).all()
    assert [e.page_count for e in events] == [2, 3]
    assert all(e.shipment_type == "Sea Import" for e in events)
    assert all(e.job_reference == "JOB-LEDGER" for e in events)

    usage = compute_usage(db_session, tenant.id, datetime.now(timezone.utc).date().replace(day=1))
    assert usage.total_pages == 5


def test_deleting_the_whole_job_does_not_erase_its_billing_history(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-ledger2@example.com")
    login(client, op.email)

    resp = client.post(
        f"/api/v1/jobs/{job.id}/documents/{tdoc.id}/upload",
        files={"file": ("invoice.pdf", _pdf_bytes(4), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    job_id = job.id

    resp = client.delete(f"/api/v1/jobs/{job_id}")
    assert resp.status_code == 204, resp.text
    db_session.expire_all()  # the delete happened through the endpoint's own session
    assert db_session.get(Job, job_id) is None  # the job itself is really gone

    # The ledger row has no FK to Job - it survives the job's own hard delete.
    event = db_session.query(BillablePageEvent).filter(BillablePageEvent.job_id == job_id).one()
    assert event.page_count == 4

    usage = compute_usage(db_session, tenant.id, datetime.now(timezone.utc).date().replace(day=1))
    assert usage.total_pages == 4
    # The job itself is gone, so it no longer counts toward "jobs created this period" -
    # only the already-incurred page cost survives, which is exactly the intended behavior.
    assert usage.total_jobs == 0


def test_page_count_zero_writes_no_ledger_event(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc)
    op = make_user(db_session, role="operator", tenant=tenant, email="op-ledger3@example.com")
    login(client, op.email)

    with patch("app.api.v1.jobs._render_pages", return_value=0):
        resp = client.post(
            f"/api/v1/jobs/{job.id}/documents/{tdoc.id}/upload",
            files={"file": ("invoice.pdf", _pdf_bytes(1), "application/pdf")},
        )
    assert resp.status_code == 200, resp.text
    assert db_session.query(BillablePageEvent).filter(BillablePageEvent.job_id == job.id).count() == 0


def test_smart_upload_writes_a_ledger_event_per_matched_file(client, db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="ready", mode="Air Import")
    db_session.add(group)
    db_session.flush()
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add(tdoc)
    db_session.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-SMART-LEDGER", status="extracted")
    db_session.add(job)
    db_session.flush()
    db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id, page_count=0))
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-ledger4@example.com")
    login(client, op.email)

    with patch("app.api.v1.jobs.ocr_page_image", return_value={"text": "some text", "tokens": []}), \
         patch("app.api.v1.jobs.assign_documents_detailed",
               return_value=[[{"key": tdoc.id, "pages": [1, 2], "evidence": "e"}]]):
        resp = client.post(
            f"/api/v1/jobs/{job.id}/smart-upload",
            files=[("files", ("inv.pdf", _pdf_bytes(2), "application/pdf"))],
        )
    assert resp.status_code == 200, resp.text

    event = db_session.query(BillablePageEvent).filter(BillablePageEvent.job_id == job.id).one()
    assert event.page_count == 2
    assert event.shipment_type == "Air Import"


def test_jobs_created_in_a_later_month_than_their_page_events_still_bill_the_pages_in_the_right_month(
    client, db_session,
):
    # A page re-uploaded well after a job was first created must bill in the month it was
    # actually (re-)processed, not the job's own creation month.
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_job(db_session, tenant, group, tdoc)
    job.created_at = job.created_at - timedelta(days=60)
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-ledger5@example.com")
    login(client, op.email)
    resp = client.post(
        f"/api/v1/jobs/{job.id}/documents/{tdoc.id}/upload",
        files={"file": ("invoice.pdf", _pdf_bytes(6), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text

    this_month = datetime.now(timezone.utc).date().replace(day=1)
    two_months_ago = (job.created_at.date()).replace(day=1)

    usage_this_month = compute_usage(db_session, tenant.id, this_month)
    assert usage_this_month.total_pages == 6
    assert usage_this_month.total_jobs == 0  # the job itself was created two months ago

    usage_old_month = compute_usage(db_session, tenant.id, two_months_ago)
    assert usage_old_month.total_pages == 0
    assert usage_old_month.total_jobs == 1
