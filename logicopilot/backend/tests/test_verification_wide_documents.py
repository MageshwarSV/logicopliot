"""A "whole job" document (set_index None - a bill of lading with no invoice number of its
own to be paired on) can carry more than one real value for the same mark when there is more
than one such file on the job: three separate bills of lading, one per invoice. Comparing only
the FIRST such value against every invoice set used to report a false mismatch whenever the
invoice actually matched a LATER file, and a false "missing" whenever the first file's own
value was blank while a later one held the real one - "the document has the value, it just
didn't get compared". _best_cross_compare tries every (source, target) pair and keeps the best
result instead."""
from app.api.v1.jobs import _best_cross_compare, _verification_findings
from app.models.cross_doc_link import CrossDocLink
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


# ---- _best_cross_compare, in isolation --------------------------------------------------------

def test_best_cross_compare_finds_a_match_even_if_it_is_not_the_first_candidate():
    status, sv, tv = _best_cross_compare(
        ["DKA2608485", "EGLV 150650077508", "EGLV150650077508"], ["EGLV150650077508"])
    assert status == "match"
    assert tv == "EGLV150650077508"


def test_best_cross_compare_reports_mismatch_when_nothing_agrees():
    status, sv, tv = _best_cross_compare(["AAA111", "BBB222"], ["ZZZ999"])
    assert status == "mismatch"


def test_best_cross_compare_reports_missing_when_every_candidate_is_none():
    status, sv, tv = _best_cross_compare([None, None], [None])
    assert status == "missing"


def test_best_cross_compare_skips_a_blank_first_candidate_to_find_the_real_one():
    """The old wide_vals.setdefault() bug: if the FIRST file processed for a mark happened to
    have no value, that None was permanently cached and every later file's real value was
    lost, even though it was right there in the list."""
    status, sv, tv = _best_cross_compare([None, "SNLGSKDLU63601"], ["SNLGSKDLU63601"])
    assert status == "match"


# ---- _verification_findings, end to end --------------------------------------------------------

def _make_group(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_doc(db_session, tenant, group, name):
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name=name, doc_type=name)
    db_session.add(doc)
    db_session.commit()
    db_session.refresh(doc)
    return doc


def _make_mark(db_session, tenant, doc, label):
    mark = FieldMark(tenant_id=tenant.id, document_id=doc.id, label_name=label,
                     page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    db_session.add(mark)
    db_session.commit()
    db_session.refresh(mark)
    return mark


def _make_job(db_session, tenant, group):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-WIDE", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_a_later_bl_file_is_correctly_matched_against_its_own_invoice_set(db_session):
    """Three bills of lading on one job (none carrying an invoice number, so all three land
    as set_index=None "whole job" values) - packing list 2's HBL number matches the THIRD bl
    file, not the first. That must read as a match, not the old false mismatch/missing."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    bl_doc = _make_doc(db_session, tenant, group, "Bill of lading")
    pl_doc = _make_doc(db_session, tenant, group, "Packing List")
    bl_hbl = _make_mark(db_session, tenant, bl_doc, "HBL No")
    pl_hbl = _make_mark(db_session, tenant, pl_doc, "HBL No")
    db_session.add(CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                                source_mark_id=bl_hbl.id, target_mark_id=pl_hbl.id))
    db_session.commit()

    job = _make_job(db_session, tenant, group)
    # Three separate BL files, none tied to a set.
    for val in ("DKA2608485", "EGLV150650077508", "SNLGSKDLU63608"):
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=bl_hbl.id,
                                     label_name="HBL No", extracted_value=val, set_index=None))
    # Packing list 2's own HBL matches the THIRD bl file, not the first.
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=pl_hbl.id,
                                 label_name="HBL No", extracted_value="SNLGSKDLU63608", set_index=2))
    db_session.commit()

    findings = _verification_findings(db_session, job)
    row = next(f for f in findings if f["set_index"] == 2)
    assert row["status"] == "match"
    assert row["target_value"] == "SNLGSKDLU63608"


def test_a_single_wide_value_still_compares_normally(db_session):
    """Regression: the ordinary case (one bl file, no multi-file wide bucket at all) is
    unaffected by the fix."""
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    bl_doc = _make_doc(db_session, tenant, group, "Bill of lading")
    pl_doc = _make_doc(db_session, tenant, group, "Packing List")
    bl_hbl = _make_mark(db_session, tenant, bl_doc, "HBL No")
    pl_hbl = _make_mark(db_session, tenant, pl_doc, "HBL No")
    db_session.add(CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                                source_mark_id=bl_hbl.id, target_mark_id=pl_hbl.id))
    db_session.commit()

    job = _make_job(db_session, tenant, group)
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=bl_hbl.id,
                                 label_name="HBL No", extracted_value="ABC123", set_index=None))
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=pl_hbl.id,
                                 label_name="HBL No", extracted_value="XYZ999", set_index=1))
    db_session.commit()

    findings = _verification_findings(db_session, job)
    assert len(findings) == 1
    assert findings[0]["status"] == "mismatch"


def test_no_wide_files_at_all_still_reports_missing_not_an_error(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    bl_doc = _make_doc(db_session, tenant, group, "Bill of lading")
    pl_doc = _make_doc(db_session, tenant, group, "Packing List")
    bl_hbl = _make_mark(db_session, tenant, bl_doc, "HBL No")
    pl_hbl = _make_mark(db_session, tenant, pl_doc, "HBL No")
    db_session.add(CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                                source_mark_id=bl_hbl.id, target_mark_id=pl_hbl.id))
    db_session.commit()
    job = _make_job(db_session, tenant, group)

    findings = _verification_findings(db_session, job)
    assert findings == []
