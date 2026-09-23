"""_compare_rows: a bill of lading's one aggregate gross weight legitimately has fewer "rows"
than a weight list's per-carton breakdown - an unequal row count for a column of plain numbers
compares the TOTAL instead of failing outright, and only disagrees if the totals themselves
don't add up. A column of text (descriptions, part codes) has no sensible total and keeps the
strict row-count rule."""
from app.api.v1.jobs import _compare_rows, _verification_findings
from app.models.cross_doc_link import CrossDocLink
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


# ---- _compare_rows, in isolation --------------------------------------------------------

def test_unequal_row_count_matches_when_the_totals_agree():
    status, sv, tv = _compare_rows(["499.0K"], ["120.5", "130.0", "125.5", "123.0"])
    assert status == "match"


def test_unequal_row_count_is_a_real_mismatch_when_totals_disagree():
    status, sv, tv = _compare_rows(["500"], ["120", "130", "125", "100"])
    assert status == "mismatch"


def test_unequal_row_count_of_text_keeps_the_strict_rule():
    """A description or part code has no sensible total - summing them makes no sense, so
    an unequal count there is still reported exactly as before."""
    status, sv, tv = _compare_rows(["PE MULTILAYER PLASTIC FILM"],
                                   ["PE MULTILAYER PLASTIC FILM", "PE MULTILAYER PLASTIC FILM"])
    assert status == "mismatch"


def test_equal_row_count_is_unaffected_by_the_numeric_total_change():
    status, sv, tv = _compare_rows(["100", "200"], ["100", "200"])
    assert status == "match"
    assert sv == "2 rows, all matched"


def test_small_rounding_difference_in_totals_is_still_a_match():
    status, sv, tv = _compare_rows(["999.6"], ["500.0", "500.0"])
    assert status == "match"


# ---- end to end, through _verification_findings ------------------------------------------

def test_bl_summary_weight_matches_weight_list_breakdown_by_total(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)

    bl_doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Bill of Lading",
                              doc_type="Bill of Lading")
    wl_doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Weight list",
                              doc_type="Weight list")
    db_session.add_all([bl_doc, wl_doc])
    db_session.commit()
    db_session.refresh(bl_doc)
    db_session.refresh(wl_doc)

    bl_mark = FieldMark(tenant_id=tenant.id, document_id=bl_doc.id, label_name="Gross weight",
                        page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    wl_mark = FieldMark(tenant_id=tenant.id, document_id=wl_doc.id, label_name="Gross weight",
                        page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    db_session.add_all([bl_mark, wl_mark])
    db_session.commit()
    db_session.refresh(bl_mark)
    db_session.refresh(wl_mark)

    db_session.add(CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                                source_mark_id=bl_mark.id, target_mark_id=wl_mark.id))
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-TOTALS", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    # One aggregate row on the bill of lading.
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=bl_mark.id,
                                 label_name="Gross weight", extracted_value="510.25",
                                 set_index=1, row_index=1))
    # Four per-carton rows on the weight list that sum to the same total.
    for i, v in enumerate(["120.00", "130.25", "125.00", "135.00"], start=1):
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=wl_mark.id,
                                     label_name="Gross weight", extracted_value=v,
                                     set_index=1, row_index=i))
    db_session.commit()

    findings = _verification_findings(db_session, job)
    assert len(findings) == 1
    assert findings[0]["status"] == "match"
