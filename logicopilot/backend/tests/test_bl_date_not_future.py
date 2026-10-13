"""A Bill of Lading's own issue/on-board date can never be in the future - the shipment has
already been made by the time the document exists. Found live on JOB-3DA007: an Expeditors
sea waybill printed "10/11/26", read day-first (every date prompt's fixed rule) as 10 Nov
2026 - a date still in the future - when the carrier actually meant 11 Oct 2026,
month-first. _clamp_bl_date_not_future is the deterministic backstop: when the day-first
reading lands after today, swap the day and month back, since a BL can only be dated today
or earlier."""
from datetime import datetime as real_datetime, timezone
from unittest.mock import patch

from app.api.v1.jobs import _clamp_bl_date_not_future
from app.models.custom_field import CustomField
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user


def _frozen_now(iso_date: str):
    year, month, day = (int(p) for p in iso_date.split("-"))
    return patch(
        "app.api.v1.jobs.datetime",
        **{
            "now.return_value": real_datetime(year, month, day, tzinfo=timezone.utc),
            "strptime": real_datetime.strptime,
        },
    )


def test_future_day_first_reading_is_swapped_to_month_first():
    with _frozen_now("2026-10-14"):
        assert _clamp_bl_date_not_future("HBL Date", "10-Nov-2026") == "11-Oct-2026"


def test_past_date_is_left_unchanged():
    with _frozen_now("2026-10-14"):
        assert _clamp_bl_date_not_future("HBL Date", "05-Jan-2026") == "05-Jan-2026"


def test_todays_date_is_left_unchanged():
    with _frozen_now("2026-10-14"):
        assert _clamp_bl_date_not_future("HBL Date", "14-Oct-2026") == "14-Oct-2026"


def test_future_date_with_day_over_12_cannot_be_swapped_so_is_kept():
    # Day 25 can never be read as a month - the order was never ambiguous here, so there is
    # nothing to correct even though the date is genuinely in the future.
    with _frozen_now("2026-10-14"):
        assert _clamp_bl_date_not_future("HBL Date", "25-Dec-2026") == "25-Dec-2026"


def test_swap_that_is_still_future_is_rejected_original_kept():
    # Both readings land after "today" here - swapping would not actually fix anything, so
    # the original (still wrong, but not made worse) is kept rather than guessed at further.
    with _frozen_now("2026-01-05"):
        assert _clamp_bl_date_not_future("HBL Date", "10-Nov-2026") == "10-Nov-2026"


def test_non_date_value_is_returned_unchanged():
    with _frozen_now("2026-10-14"):
        assert _clamp_bl_date_not_future("HBL Date", "not a date") == "not a date"


def test_blank_or_none_is_returned_unchanged():
    with _frozen_now("2026-10-14"):
        assert _clamp_bl_date_not_future("HBL Date", "") == ""
        assert _clamp_bl_date_not_future("HBL Date", None) is None


def test_label_outside_the_gate_is_never_touched():
    # Scoped deliberately to the BL's own issue-date fields - an unrelated date field with
    # the exact same raw (wrong) value must not be silently rewritten too.
    with _frozen_now("2026-10-14"):
        assert _clamp_bl_date_not_future("Invoice Date", "10-Nov-2026") == "10-Nov-2026"


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved", mode="Sea Import")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading", doc_type="BL")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    return group, tdoc


def _make_extracted_job(db_session, tenant, group, tdoc, reference="JOB-BLDATE"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracted",
             validation_approved=True)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1,
                     extracted_json={"text": "LADEN ON BOARD 10/11/26", "pages": ["LADEN ON BOARD 10/11/26"]})
    db_session.add(jd)
    db_session.commit()
    return job


def test_recompute_endpoint_clamps_a_future_hbl_date(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="HBL Date",
                     kind="ai", ai_prompt="Return the Bill of Lading's own issue date.")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa-bldate@example.com")
    login(client, "sa-bldate@example.com")

    with _frozen_now("2026-10-14"), \
         patch("app.core.llm.compute_custom_field", return_value="10-Nov-2026"):
        resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["extracted_value"] == "11-Oct-2026"


def test_recompute_endpoint_leaves_other_date_labels_untouched(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc = _make_template(db_session, tenant)
    job = _make_extracted_job(db_session, tenant, group, tdoc)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Invoice Date",
                     kind="ai", ai_prompt="Return the invoice's own date.")
    db_session.add(cf)
    db_session.commit()
    db_session.refresh(cf)

    make_user(db_session, role=SUPER_ADMIN, email="sa-invdate@example.com")
    login(client, "sa-invdate@example.com")

    with _frozen_now("2026-10-14"), \
         patch("app.core.llm.compute_custom_field", return_value="10-Nov-2026"):
        resp = client.post(f"/api/v1/jobs/{job.id}/custom-fields/{cf.id}/recompute")

    assert resp.status_code == 200, resp.text
    assert resp.json()[0]["extracted_value"] == "10-Nov-2026"
