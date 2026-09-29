"""Reproduces a live production bug: an already-extracted job (JOB-7EED74) ended up with
ZERO mark-based JobFieldValue rows (every row with a non-null template_document_id) while
every tenant CustomField value (custom_field_id set, template_document_id null) survived
untouched, with nothing anywhere saying the job needed a fresh look.

run_extraction itself cannot cause this on its own — it deletes ALL of a job's JobFieldValue
rows up front (see the "Clear any prior run" comment, ~jobs.py:4128) and only commits once, at
the very end, after both the mark loop AND the custom-field loop have run; a mid-function
exception rolls the whole attempt back atomically (nothing partially written), it does not
selectively erase only the mark rows.

The real culprit is a SEPARATE, unrelated feature: app/api/v1/custom_filter_pages.py's
`_sweep_old_jobs_for_reference`, which retroactively re-applies a newly-uploaded Super Admin
"custom filter page" to every already-extracted job in the system (`Job.status.notin_(
("draft", "extracting", "completed", "duplicate"))` — "Hold"/"extracted"/etc. are all in
scope). For any JobDocument whose OCR'd page text near-matches the new reference, it calls
`_remove_document_file`, which deletes JobFieldValue rows keyed on job_document_id. Every
mark-based row (single- or multi-value) IS written with job_document_id set, so it is wiped.
Every CustomField-based row (hardcoded/lookup/ai) is written WITHOUT job_document_id — it was
never touched by that delete, so it survived, stranded, looking valid.

Fixed: _remove_document_file now ALSO clears every custom-field row on a non-draft job and
sets Job.needs_reextraction, so nothing stale is left behind and the job visibly asks for a
fresh Extract - the same real bug this test file was written to prove, now with the fix's
behaviour asserted instead of the bug's."""
from unittest.mock import patch

from app.api.v1.custom_filter_pages import _sweep_old_jobs_for_reference
from app.api.v1.jobs import run_extraction
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant

REFERENCE_TEXT = "BOILERPLATE COVER SHEET - CONFIDENTIAL - THIS TRANSMISSION IS INTENDED ONLY"
NON_MATCHING_TEXT = "Shipper: DTDS TECHNOLOGY PTE LTD, Invoice No: INV-2026-001, Total: USD 500.00"


def _make_template(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    tdoc_bl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading", doc_type="BL")
    tdoc_pl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List", doc_type="PL")
    db_session.add_all([tdoc_bl, tdoc_pl])
    db_session.commit()
    db_session.refresh(tdoc_bl)
    db_session.refresh(tdoc_pl)

    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc_bl.id, label_name="Port of delivery",
                     page_number=1, x=0.1, y=0.1, width=0.2, height=0.05)
    db_session.add(mark)
    db_session.commit()

    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Shipping Line",
                     kind="hardcoded", hardcoded_value="KSS ROADWAYS")
    db_session.add(cf)
    db_session.commit()
    return group, tdoc_bl, tdoc_pl


def _make_job_with_two_documents(db_session, tenant, group, tdoc_bl, tdoc_pl, **kw):
    kw.setdefault("status", "extracting")
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-7EED74", **kw)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    jd_bl = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc_bl.id,
                        file_path="fake/bl.pdf", page_count=1, file_index=0)
    jd_pl = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc_pl.id,
                        file_path="fake/pl.pdf", page_count=1, file_index=1)
    db_session.add_all([jd_bl, jd_pl])
    db_session.commit()
    db_session.refresh(jd_bl)
    db_session.refresh(jd_pl)
    return job, jd_bl, jd_pl


def _mark_rows(db_session, job):
    return (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.template_document_id.isnot(None))
        .all()
    )


def _custom_field_rows(db_session, job):
    return (
        db_session.query(JobFieldValue)
        .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id.isnot(None))
        .all()
    )


def _run_with_fake_ocr(db_session, job, jd_bl):
    def _ocr_for(job_doc_dir, page):
        # get_page_ocr is keyed on the job-document directory, not the JobDocument object
        # itself — infer which document this is from the path, same as run_extraction does.
        text = REFERENCE_TEXT if str(jd_bl.id) in str(job_doc_dir) else NON_MATCHING_TEXT
        return {"layout_text": text, "text": text, "tokens": []}

    with patch("app.api.v1.jobs.get_page_ocr", side_effect=_ocr_for), \
         patch("app.api.v1.jobs.extract_document_fields",
               return_value={"Port of delivery": "Singapore"}):
        run_extraction(db_session, job)


def test_filter_page_sweep_now_clears_stale_custom_fields_and_flags_reextraction(db_session):
    tenant = make_tenant(db_session)
    group, tdoc_bl, tdoc_pl = _make_template(db_session, tenant)
    job, jd_bl, jd_pl = _make_job_with_two_documents(db_session, tenant, group, tdoc_bl, tdoc_pl)

    _run_with_fake_ocr(db_session, job, jd_bl)

    # Sanity: run_extraction really did write both kinds of row, proving the bug is not
    # simply "extraction found nothing", and the flag starts clean.
    mark_rows_before = _mark_rows(db_session, job)
    cf_rows_before = _custom_field_rows(db_session, job)
    assert len(mark_rows_before) == 1
    assert mark_rows_before[0].extracted_value == "Singapore"
    assert len(cf_rows_before) == 1
    assert cf_rows_before[0].extracted_value == "KSS ROADWAYS"
    assert job.status == "extracted"
    assert job.needs_reextraction is False

    # A Super Admin uploads a new custom filter page whose text happens to near-match the
    # Bill of Lading's own (persisted) OCR'd content — e.g. a cover sheet or boilerplate
    # paragraph a later admin flagged as junk after this job had already been extracted.
    _sweep_old_jobs_for_reference(db_session, REFERENCE_TEXT)

    db_session.refresh(jd_bl)
    db_session.refresh(jd_pl)
    db_session.refresh(job)

    # The Bill of Lading's own mark row is correctly gone (its document was removed) - that
    # part was always right. What's fixed: the custom field row is now cleared too, instead
    # of surviving stranded, and the job is flagged as needing a fresh Extract.
    assert _mark_rows(db_session, job) == []
    assert _custom_field_rows(db_session, job) == []
    assert job.needs_reextraction is True

    # The job itself is not deleted (Packing List is untouched, so it still has a real
    # document) - it just needs re-extracting, matching production where the job is still
    # gettable via GET /jobs/{id} and sitting in "Hold".
    assert db_session.get(Job, job.id) is not None
    assert jd_bl.file_path is None
    assert jd_pl.file_path == "fake/pl.pdf"


def test_removing_a_file_from_a_draft_job_touches_neither_custom_fields_nor_the_flag(db_session):
    """A job that has never been extracted yet has nothing stale to clear - losing an upload
    before the first Extract is completely normal (an operator swapping the wrong file for
    the right one) and must not spuriously flag the job or wipe fields that were never
    computed from anything in the first place."""
    from app.api.v1.jobs import _remove_document_file

    tenant = make_tenant(db_session)
    group, tdoc_bl, tdoc_pl = _make_template(db_session, tenant)
    job, jd_bl, jd_pl = _make_job_with_two_documents(
        db_session, tenant, group, tdoc_bl, tdoc_pl, status="draft")

    _remove_document_file(db_session, job, jd_bl)
    db_session.commit()

    assert job.needs_reextraction is False
    assert _custom_field_rows(db_session, job) == []


def test_run_extraction_clears_the_flag_on_a_fresh_run(db_session):
    tenant = make_tenant(db_session)
    group, tdoc_bl, tdoc_pl = _make_template(db_session, tenant)
    job, jd_bl, jd_pl = _make_job_with_two_documents(db_session, tenant, group, tdoc_bl, tdoc_pl)
    job.needs_reextraction = True
    db_session.commit()

    _run_with_fake_ocr(db_session, job, jd_bl)

    db_session.refresh(job)
    assert job.needs_reextraction is False


# ---- a re-run must never silently discard a correction ---------------------------------------

def test_rerunning_extraction_keeps_an_existing_correction(db_session):
    """"Re-run extraction" re-reads the documents - it does not mean "and throw away whatever
    a human already typed". Found live on a real job: an operator had made 10 corrections and
    approved every document, and the only way to pick up one document's missed data (see
    test_upload_blocked_during_extraction.py for the actual race) was a full re-extract, which
    would have silently wiped every one of them."""
    tenant = make_tenant(db_session)
    group, tdoc_bl, tdoc_pl = _make_template(db_session, tenant)
    job, jd_bl, jd_pl = _make_job_with_two_documents(db_session, tenant, group, tdoc_bl, tdoc_pl)

    _run_with_fake_ocr(db_session, job, jd_bl)

    mark_row = _mark_rows(db_session, job)[0]
    mark_row.corrected_value = "Singapore Port (corrected)"
    db_session.commit()

    # A second run re-reads the SAME document, coming back with the same raw value again -
    # exactly what happens when an operator presses Re-run extraction on a job they've
    # already corrected something on.
    _run_with_fake_ocr(db_session, job, jd_bl)

    mark_row_after = _mark_rows(db_session, job)[0]
    assert mark_row_after.extracted_value == "Singapore"
    assert mark_row_after.corrected_value == "Singapore Port (corrected)"
    assert mark_row_after.value == "Singapore Port (corrected)"


def test_rerunning_extraction_leaves_a_newly_added_documents_fields_uncorrected(db_session):
    """A document that didn't exist during the FIRST run (the one this session's race
    condition let slip past silently) has no prior correction to restore - it must come back
    freshly extracted, not blocked from ever getting real data because of a key that matches
    nothing."""
    tenant = make_tenant(db_session)
    group, tdoc_bl, tdoc_pl = _make_template(db_session, tenant)
    job, jd_bl, jd_pl = _make_job_with_two_documents(db_session, tenant, group, tdoc_bl, tdoc_pl)

    _run_with_fake_ocr(db_session, job, jd_bl)
    mark_row = _mark_rows(db_session, job)[0]
    mark_row.corrected_value = "Singapore Port (corrected)"
    db_session.commit()

    # A second Bill of Lading is added between the two runs - the same shape as the second
    # Packing List that got missed in production.
    jd_bl2 = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc_bl.id,
                         file_path="fake/bl2.pdf", page_count=1, file_index=2)
    db_session.add(jd_bl2)
    db_session.commit()
    db_session.refresh(jd_bl2)

    def _ocr_for(job_doc_dir, page):
        if str(jd_bl.id) in str(job_doc_dir):
            text = REFERENCE_TEXT
        elif str(jd_bl2.id) in str(job_doc_dir):
            text = "second bill of lading, unrelated content"
        else:
            text = NON_MATCHING_TEXT
        return {"layout_text": text, "text": text, "tokens": []}

    with patch("app.api.v1.jobs.get_page_ocr", side_effect=_ocr_for), \
         patch("app.api.v1.jobs.extract_document_fields",
               return_value={"Port of delivery": "Singapore"}):
        run_extraction(db_session, job)

    rows_by_doc = {
        r.job_document_id: r for r in _mark_rows(db_session, job) if r.job_document_id in (jd_bl.id, jd_bl2.id)
    }
    assert rows_by_doc[jd_bl.id].corrected_value == "Singapore Port (corrected)"
    assert rows_by_doc[jd_bl2.id].corrected_value is None
    assert rows_by_doc[jd_bl2.id].extracted_value == "Singapore"
