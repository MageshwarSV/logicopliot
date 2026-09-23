"""_best_rows_to_scalar_compare: the reported bug — an Invoice gives quantity PER PRODUCT LINE
(several rows) while a Packing List gives only ONE aggregate total_quantity (a genuine
job-level value, never split per line on that document at all). The old routing read the
scalar side as "zero rows" — identical to the field being genuinely absent — and reported
"missing" without ever looking at the real value sitting right there. This sums the row side
(same numeric_total already used for two row lists of unequal length) and compares that sum
against the other side's own value, with the same numeric tolerance _compare_rows already
uses."""
from app.api.v1.jobs import _best_rows_to_scalar_compare, _verification_findings
from app.models.cross_doc_link import CrossDocLink
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


# ---- _best_rows_to_scalar_compare, in isolation --------------------------------------------

def test_rows_sum_matches_the_scalar():
    status, sv, tv = _best_rows_to_scalar_compare([["16", "20", "1152", "1960"]], ["3148"])
    assert status == "match"
    assert tv == "3148"


def test_rows_sum_disagrees_with_the_scalar():
    status, sv, tv = _best_rows_to_scalar_compare([["16", "20"]], ["500"])
    assert status == "mismatch"


def test_small_rounding_difference_is_still_a_match():
    status, sv, tv = _best_rows_to_scalar_compare([["500.0", "500.0"]], ["999.6"])
    assert status == "match"


def test_non_numeric_row_column_has_no_sensible_total():
    """Descriptions/part codes have no total to compare against a scalar - "missing", not a
    guessed match or mismatch."""
    status, sv, tv = _best_rows_to_scalar_compare(
        [["COVER FRONT LH", "COVER REAR LH"]], ["some description"])
    assert status == "missing"


def test_a_non_numeric_scalar_is_also_missing():
    status, sv, tv = _best_rows_to_scalar_compare([["100", "200"]], ["not a number"])
    assert status == "missing"


def test_no_scalar_candidate_at_all_is_missing_not_an_exception():
    status, sv, tv = _best_rows_to_scalar_compare([["100", "200"]], [None])
    assert status == "missing"


def test_no_row_candidates_at_all_is_missing():
    status, sv, tv = _best_rows_to_scalar_compare([], ["300"])
    assert status == "missing"


def test_tries_every_candidate_pair_and_keeps_the_best():
    # Two files' worth of rows on one side (a whole-job document with no invoice number of
    # its own), two candidate scalars on the other - the real pairing exists somewhere in the
    # cross product and must be found.
    status, sv, tv = _best_rows_to_scalar_compare(
        [["999", "999"], ["10", "20"]], ["999999", "30"])
    assert status == "match"


# ---- end to end, through _verification_findings ---------------------------------------------

def _make_group_with_invoice_and_packing_list(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    inv_doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    pl_doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List", doc_type="packing_list")
    db_session.add_all([inv_doc, pl_doc])
    db_session.commit()
    db_session.refresh(inv_doc)
    db_session.refresh(pl_doc)
    inv_mark = FieldMark(tenant_id=tenant.id, document_id=inv_doc.id, label_name="item_quantity",
                         page_number=1, x=0.1, y=0.1, width=0.1, height=0.1, is_multi_value=True)
    pl_mark = FieldMark(tenant_id=tenant.id, document_id=pl_doc.id, label_name="total_quantity",
                        page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    db_session.add_all([inv_mark, pl_mark])
    db_session.commit()
    db_session.refresh(inv_mark)
    db_session.refresh(pl_mark)
    db_session.add(CrossDocLink(tenant_id=tenant.id, group_id=group.id,
                                source_mark_id=inv_mark.id, target_mark_id=pl_mark.id))
    db_session.commit()
    return group, inv_mark, pl_mark


def test_invoice_per_line_quantity_matches_packing_lists_one_total(db_session):
    """The exact reported scenario: Invoice gives quantity per product line, Packing List
    gives one aggregate total_quantity - and they genuinely agree."""
    tenant = make_tenant(db_session)
    group, inv_mark, pl_mark = _make_group_with_invoice_and_packing_list(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ROWSCALAR1", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    for i, v in enumerate(["16", "20", "1152", "1960", "1820", "2688", "20429"], start=1):
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=inv_mark.id,
                                     label_name="item_quantity", extracted_value=v,
                                     set_index=1, row_index=i))
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=pl_mark.id,
                                 label_name="total_quantity", extracted_value="28085", set_index=1))
    db_session.commit()

    findings = _verification_findings(db_session, job)
    assert len(findings) == 1
    assert findings[0]["status"] == "match"


def test_invoice_per_line_quantity_disagrees_with_packing_lists_total(db_session):
    tenant = make_tenant(db_session)
    group, inv_mark, pl_mark = _make_group_with_invoice_and_packing_list(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ROWSCALAR2", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    for i, v in enumerate(["16", "20"], start=1):
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=inv_mark.id,
                                     label_name="item_quantity", extracted_value=v,
                                     set_index=1, row_index=i))
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=pl_mark.id,
                                 label_name="total_quantity", extracted_value="9999", set_index=1))
    db_session.commit()

    findings = _verification_findings(db_session, job)
    assert len(findings) == 1
    assert findings[0]["status"] == "mismatch"


def test_missing_total_on_the_packing_list_is_reported_as_missing_not_hidden(db_session):
    """A real value exists on the Invoice side - this must surface as "missing" (couldn't be
    verified) so the operator can see it, not be silently dropped the way "neither side has
    anything at all" is."""
    tenant = make_tenant(db_session)
    group, inv_mark, pl_mark = _make_group_with_invoice_and_packing_list(db_session, tenant)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ROWSCALAR3", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    for i, v in enumerate(["16", "20"], start=1):
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=inv_mark.id,
                                     label_name="item_quantity", extracted_value=v,
                                     set_index=1, row_index=i))
    db_session.commit()

    findings = _verification_findings(db_session, job)
    assert len(findings) == 1
    assert findings[0]["status"] == "missing"
