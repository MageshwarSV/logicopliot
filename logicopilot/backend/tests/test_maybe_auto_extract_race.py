"""_maybe_auto_extract's atomic claim on the draft->extracting transition.

Found live: a job's last required documents uploaded via separate, near-simultaneous requests
(a parallel multi-file drag-and-drop, plausibly) could each reach _maybe_auto_extract having
read job.status == "draft" moments apart - an ordinary check-then-act race. Both would then
call _begin_extraction and start their own full run_extraction, and since that function's
per-row/multi-value write loops used to insert unconditionally, both runs' inserts for the
same (field, line) survived - every per-row custom field value on the job came out doubled.
This tests the fix: only one of two callers racing the same job can ever proceed."""
from unittest.mock import patch

from app.api.v1.jobs import _maybe_auto_extract
from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _make_group_with_two_required_docs(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    d1 = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice", is_required=True)
    d2 = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List", doc_type="packing_list", is_required=True)
    db_session.add_all([d1, d2])
    db_session.commit()
    db_session.refresh(d1)
    db_session.refresh(d2)
    return group, d1, d2


def _make_draft_job_with_both_uploaded(db_session, tenant, group, d1, d2):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-RACE", status="draft")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd1 = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=d1.id,
                      file_path="fake/invoice.pdf", page_count=1)
    jd2 = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=d2.id,
                      file_path="fake/packing.pdf", page_count=1)
    db_session.add_all([jd1, jd2])
    db_session.commit()
    return job


def test_only_one_of_two_racing_callers_starts_extraction(db_session):
    """Both callers hold the SAME in-memory job object with status still "draft", exactly as
    two separate request handlers each doing their own _load_job would - the atomic UPDATE is
    what has to resolve this, not the plain attribute check, which both would pass."""
    tenant = make_tenant(db_session)
    group, d1, d2 = _make_group_with_two_required_docs(db_session, tenant)
    job = _make_draft_job_with_both_uploaded(db_session, tenant, group, d1, d2)
    assert job.status == "draft"

    with patch("app.api.v1.jobs._start_extraction_background") as mock_start:
        _maybe_auto_extract(db_session, job)
        # Second caller's own job object still reads "draft" in memory, unaware the first
        # caller's UPDATE already landed - the exact shape of the race.
        job.status = "draft"
        _maybe_auto_extract(db_session, job)

    assert mock_start.call_count == 1


def test_a_single_caller_still_starts_extraction_normally(db_session):
    tenant = make_tenant(db_session)
    group, d1, d2 = _make_group_with_two_required_docs(db_session, tenant)
    job = _make_draft_job_with_both_uploaded(db_session, tenant, group, d1, d2)

    with patch("app.api.v1.jobs._start_extraction_background") as mock_start:
        _maybe_auto_extract(db_session, job)

    mock_start.assert_called_once()
    assert job.status == "extracting"


def test_does_not_fire_when_a_required_document_is_still_missing(db_session):
    tenant = make_tenant(db_session)
    group, d1, d2 = _make_group_with_two_required_docs(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-PARTIAL", status="draft")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd1 = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=d1.id,
                      file_path="fake/invoice.pdf", page_count=1)
    db_session.add(jd1)
    db_session.commit()

    with patch("app.api.v1.jobs._start_extraction_background") as mock_start:
        _maybe_auto_extract(db_session, job)

    mock_start.assert_not_called()
    assert job.status == "draft"


def test_does_not_fire_a_second_time_once_already_extracted(db_session):
    """The ordinary non-race case this function was always meant to guard: an upload made to
    replace one document on an already-extracted job must not restart extraction on its own."""
    tenant = make_tenant(db_session)
    group, d1, d2 = _make_group_with_two_required_docs(db_session, tenant)
    job = _make_draft_job_with_both_uploaded(db_session, tenant, group, d1, d2)
    job.status = "extracted"
    db_session.commit()

    with patch("app.api.v1.jobs._start_extraction_background") as mock_start:
        _maybe_auto_extract(db_session, job)

    mock_start.assert_not_called()
