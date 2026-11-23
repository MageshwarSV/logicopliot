"""Super Admin -> Extraction Corrections: operator edits are recorded with before / after / AI
value, and a field corrected on 3+ different jobs is reported as a recurring extraction error."""
from app.models.field_mark import FieldMark
from app.models.field_value_edit import FieldValueEdit
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR
from tests.conftest import make_tenant, make_user


def _setup(db, tenant, n_jobs=3):
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="approved", mode="Air Import")
    db.add(group)
    db.commit()
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Air Waybill", doc_type="AWB")
    db.add(tdoc)
    db.commit()
    mark = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name="HBL No",
                     page_number=1, x=0.1, y=0.1, width=0.1, height=0.1)
    db.add(mark)
    db.commit()
    fvs = []
    for i in range(n_jobs):
        job = Job(tenant_id=tenant.id, group_id=group.id, reference=f"JOB-C{i}", status="extracted")
        db.add(job)
        db.commit()
        jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                         file_path="fake.pdf", page_count=1)
        db.add(jd)
        db.commit()
        fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, mark_id=mark.id, job_document_id=jd.id,
                           template_document_id=tdoc.id, label_name="HBL No", extracted_value=f"9567{i}")
        db.add(fv)
        db.commit()
        fvs.append(fv)
    return group, tdoc, fvs


def _edit(db, fv, value, user=None, source="edit"):
    """What jobs.correct_field_value does: record, then save."""
    from app.api.v1.jobs import _apply_correction
    from app.core import field_edits

    field_edits.record(db, fv, fv.value, value, user, source=source)
    _apply_correction(db, fv, value)
    db.commit()


def test_each_operator_edit_is_recorded_with_before_after_and_ai_value(db_session):
    tenant = make_tenant(db_session)
    _g, _t, fvs = _setup(db_session, tenant, n_jobs=1)
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-corr1@example.com", full_name="Nivetha")
    fv = fvs[0]
    _edit(db_session, fv, "9AN5670", op)
    _edit(db_session, fv, "9AN5670", op)          # unchanged - not an edit
    _edit(db_session, fv, "9AN56700", op)
    rows = db_session.query(FieldValueEdit).order_by(FieldValueEdit.created_at).all()
    assert [(r.before_value, r.after_value) for r in rows] == [("95670", "9AN5670"), ("9AN5670", "9AN56700")]
    assert all(r.ai_value == "95670" and r.edited_by_name == "Nivetha" and r.job_reference == "JOB-C0"
               for r in rows)


def test_field_corrected_on_three_jobs_is_reported_and_two_is_not(db_session):
    from app.core import field_edits

    tenant = make_tenant(db_session)
    group, tdoc, fvs = _setup(db_session, tenant, n_jobs=3)
    for i, fv in enumerate(fvs[:2]):
        _edit(db_session, fv, f"9AN567{i}")
    assert field_edits.summary(db_session)["fields"] == []

    # The third job was corrected BEFORE edits were logged: only its saved value differs.
    fvs[2].corrected_value = "9AN5672"
    db_session.commit()
    fields = field_edits.summary(db_session)["fields"]
    assert len(fields) == 1
    f = fields[0]
    assert (f["label_name"], f["template"], f["document"]) == ("HBL No", "Air import", "Air Waybill")
    assert f["jobs_corrected"] == 3 and f["total_edits"] == 3 and f["jobs_with_field"] == 3
    assert f["correction_rate"] == 100.0 and f["wrong"] == 3

    lines = field_edits.edits(db_session, group_id=group.id, label_name="HBL No",
                              template_document_id=tdoc.id)["lines"]
    by_job = {line["job_reference"]: line for line in lines}
    assert by_job["JOB-C0"]["ai_value"] == "95670" and by_job["JOB-C0"]["final_value"] == "9AN5670"
    assert by_job["JOB-C0"]["edit_count"] == 1 and by_job["JOB-C2"]["edit_count"] is None
    assert field_edits.summary(db_session, min_jobs=4)["fields"] == []


def test_missed_and_cleared_are_told_apart():
    from app.core.field_edits import kind_of

    assert kind_of("", "PCS") == "missed"
    assert kind_of("95670", "") == "cleared"
    assert kind_of("95670", "9AN5670") == "wrong"


def test_operator_entry_fields_are_left_out_unless_asked(db_session):
    from app.core import field_edits
    from app.models.custom_field import CustomField

    tenant = make_tenant(db_session)
    group, _tdoc, fvs = _setup(db_session, tenant, n_jobs=3)
    cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="branch_name", kind="hardcoded")
    db_session.add(cf)
    db_session.commit()
    for fv in fvs:
        db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=fv.job_id, custom_field_id=cf.id,
                                     label_name="branch_name", extracted_value="", corrected_value="Chennai"))
    db_session.commit()
    assert field_edits.summary(db_session)["fields"] == []
    shown = field_edits.summary(db_session, include_operator_fields=True)["fields"]
    assert [f["label_name"] for f in shown] == ["branch_name"]
