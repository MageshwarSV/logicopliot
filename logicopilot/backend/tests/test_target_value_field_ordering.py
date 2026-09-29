"""run_extraction's custom_fields query has no ORDER BY, so a lookup_key_label field (one
that reads ANOTHER field's value on the same job - see _resolve_target_value in jobs.py) can
be processed before the field it depends on, in the same extraction pass. When that happens
it looks up whatever value that other field held from BEFORE this run - blank, on a job
being extracted for the first time - instead of the value this same run is about to give it.

Found live: "Erp Entry Name" (kind="hardcoded", keyed on "Quotation Name" via
lookup_key_label) came back empty on a job whose Quotation Name was correctly extracted as
"KUEHNE + NAGEL PVT. LTD." in the very same run - recomputing Erp Entry Name afterwards, in
isolation, resolved it via the reference table immediately, proving the lookup itself was
never the problem, only the ORDER the two fields were processed in.
"""
from unittest.mock import patch

from app.api.v1.jobs import _job_doc_dir, run_extraction
from app.core.reference_cache import remember_reference
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant

_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100"
    "ffff03000006000557bfabd40000000049454e44ae426082"
)


def test_a_lookup_key_field_created_before_its_key_field_still_resolves(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    # A document with no marks at all is skipped entirely by run_extraction's per-document
    # loop, before it ever prepares the text a custom field's AI prompt reads - a single,
    # unrelated mark is enough to make that loop actually run for this document.
    db_session.add(FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="invoice_no",
                             page_number=1, x=0.1, y=0.1, width=0.1, height=0.1))
    db_session.commit()

    # Created FIRST on purpose - a plain query with no ORDER BY tends to return rows in
    # creation order, so this is exactly the arrangement that reproduced the bug: the
    # dependent field sorts ahead of the field it depends on unless something corrects it.
    erp_entry_name = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="Erp Entry Name",
        kind="hardcoded", hardcoded_value=None, ask_operator=True, ask_operator_required=True,
        is_target_value=True, fuzzy_match=True, lookup_key_label="Quotation Name",
    )
    quotation_name = CustomField(
        tenant_id=tenant.id, group_id=group.id, label_name="Quotation Name",
        kind="ai", ai_prompt="Read the freight forwarder's name off the invoice.",
    )
    db_session.add_all([erp_entry_name, quotation_name])
    db_session.commit()
    db_session.refresh(erp_entry_name)

    remember_reference(
        db_session, custom_field_id=erp_entry_name.id,
        match_values=["KUEHNE + NAGEL PVT. LTD."], resolved_value="KUEHNE+NAGEL PVT LTD",
    )
    db_session.commit()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-290770", status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     file_path="fake/does-not-exist.pdf", page_count=1)
    db_session.add(jd)
    db_session.commit()
    db_session.refresh(jd)
    pages_dir = _job_doc_dir(jd.id) / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    (pages_dir / "page_1.png").write_bytes(_TINY_PNG)

    with patch("app.api.v1.jobs.get_page_ocr",
              return_value={"layout_text": "some invoice text", "text": "some invoice text", "tokens": []}), \
         patch("app.api.v1.jobs.extract_document_fields", return_value={}), \
         patch("app.api.v1.jobs.extract_document_rows", return_value=[]), \
         patch("app.core.llm.compute_custom_field", return_value="KUEHNE + NAGEL PVT. LTD."):
        run_extraction(db_session, job)

    erp_entry_fv = (db_session.query(JobFieldValue)
                    .filter(JobFieldValue.job_id == job.id, JobFieldValue.custom_field_id == erp_entry_name.id)
                    .one())
    assert erp_entry_fv.extracted_value == "KUEHNE+NAGEL PVT LTD"
