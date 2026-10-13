"""Every "...Address" value entry_values_and_rows() returns is re-flowed to 71 characters per
line, breaking only at whole words - the ERP's own address box is a fixed set of discrete
lines, not a free-flowing textarea, so one long unwrapped string risks being cut off wherever
the ERP's own per-line limit falls. Applies wherever the field landed: job-level, per-row, or
per-invoice-set - the live ERP automation's own `fill` steps and the Excel workbook build both
read values/rows straight from this one shared function. The job detail API (GET /jobs/{id})
wraps the SAME way, so the operator sees an address exactly as it will actually be submitted,
not an unwrapped copy that only gets wrapped later at submission time."""
from app.api.v1.jobs import _wrap_address_lines, entry_values_and_rows
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group_and_job(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ADDRWRAP1", status="extracting")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return group, job


def test_wrap_address_lines_never_splits_a_word_and_respects_the_width():
    text = (
        "Plot No-G65, SUR NO-120,122,123,160 VADAGAL B KANCHIPURAM ROAD MALAJABAD TALUK "
        "KANCHIPURAM, TAMIL NADU 631604 INDIA"
    )
    wrapped = _wrap_address_lines(text, width=71)
    lines = wrapped.split("\n")
    assert all(len(line) <= 71 for line in lines)
    # Re-joining the lines with single spaces reconstructs every original word, in order -
    # proof nothing was split mid-word or dropped.
    assert " ".join(lines) == " ".join(text.split())


def test_wrap_address_lines_collapses_existing_line_breaks_before_rewrapping():
    text = "Plot No-G65,\nSUR NO-120,122,123,160\nVADAGAL B\nKANCHIPURAM ROAD"
    wrapped = _wrap_address_lines(text, width=71)
    # A short address well under 71 chars collapses onto ONE line, not four - the original
    # line breaks were an artifact of the source document, not an intentional 71-char wrap.
    assert "\n" not in wrapped
    assert wrapped == "Plot No-G65, SUR NO-120,122,123,160 VADAGAL B KANCHIPURAM ROAD"


def test_wrap_address_lines_moves_a_word_to_the_next_line_rather_than_splitting_it():
    # "KANCHIPURAM" (11 chars) after 60 chars of other words would land at 72 - over the
    # 71-char limit - so it moves to its own line whole rather than being cut at 71.
    text = "A" * 60 + " KANCHIPURAM"
    wrapped = _wrap_address_lines(text, width=71)
    lines = wrapped.split("\n")
    assert lines[0] == "A" * 60
    assert lines[1] == "KANCHIPURAM"


def test_wrap_address_lines_blank_input_stays_blank():
    assert _wrap_address_lines("") == ""
    assert _wrap_address_lines("   ") == ""


def test_entry_values_wraps_a_job_level_address_field(db_session):
    tenant = make_tenant(db_session)
    group, job = _make_group_and_job(db_session, tenant)
    long_address = (
        "Plot No-G65, SUR NO-120,122,123,160 VADAGAL B KANCHIPURAM ROAD MALAJABAD TALUK "
        "KANCHIPURAM, TAMIL NADU 631604 INDIA"
    )
    db_session.add(JobFieldValue(
        tenant_id=tenant.id, job_id=job.id, label_name="Supplier Address",
        extracted_value=long_address,
    ))
    db_session.add(JobFieldValue(
        tenant_id=tenant.id, job_id=job.id, label_name="Invoice No", extracted_value="INV-1",
    ))
    db_session.commit()

    values, rows = entry_values_and_rows(db_session, job)

    assert all(len(line) <= 71 for line in values["Supplier Address"].split("\n"))
    # A field whose label does not mention "address" is left exactly as extracted.
    assert values["Invoice No"] == "INV-1"


def test_entry_values_wraps_a_per_row_address_field(db_session):
    tenant = make_tenant(db_session)
    group, job = _make_group_and_job(db_session, tenant)
    long_address = "A" * 50 + " B" * 20
    db_session.add(JobFieldValue(
        tenant_id=tenant.id, job_id=job.id, label_name="product_description", row_index=1,
        extracted_value="WIDGET",
    ))
    db_session.add(JobFieldValue(
        tenant_id=tenant.id, job_id=job.id, label_name="Consignee Address", row_index=1,
        extracted_value=long_address,
    ))
    db_session.commit()

    values, rows = entry_values_and_rows(db_session, job)

    assert all(len(line) <= 71 for line in rows["Consignee Address"][0].split("\n"))


def test_job_detail_api_shows_an_address_field_wrapped(client, db_session):
    tenant = make_tenant(db_session)
    group, job = _make_group_and_job(db_session, tenant)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="BL", doc_type="BL")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                               file_path="fake/does-not-exist.pdf", page_count=1))
    long_address = (
        "Plot No-G65, SUR NO-120,122,123,160 VADAGAL B KANCHIPURAM ROAD MALAJABAD TALUK "
        "KANCHIPURAM, TAMIL NADU 631604 INDIA"
    )
    db_session.add(JobFieldValue(
        tenant_id=tenant.id, job_id=job.id, label_name="Shipper Address",
        extracted_value=long_address, template_document_id=tdoc.id,
    ))
    db_session.commit()

    op = make_user(db_session, role="operator", tenant=tenant, email="op-addrwrap1@example.com")
    login(client, op.email)

    resp = client.get(f"/api/v1/jobs/{job.id}")
    assert resp.status_code == 200, resp.text
    by_label = {fv["label_name"]: fv for fv in resp.json()["field_values"]}

    shipper = by_label["Shipper Address"]
    assert all(len(line) <= 71 for line in shipper["extracted_value"].split("\n"))
    assert all(len(line) <= 71 for line in shipper["value"].split("\n"))
    # Rejoining the wrapped lines reconstructs every original word, in order.
    assert " ".join(shipper["value"].split("\n")) == " ".join(long_address.split())
