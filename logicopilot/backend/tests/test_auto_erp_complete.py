"""Auto ERP Submission: no mandatory ERP column goes out empty (app/core/auto_erp_complete.py).
JOB-E739B0: the product "unit" was empty on all 16 lines and LogiLENS refused the import."""
from app.core.auto_erp_complete import complete_for_erp, nearest_option
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant

UNITS = ["BAG", "BOX", "KGS", "NOS", "PCS", "SET"]
CFG = {"sheets": [
    {"sheet": "ITEMS", "scope": "line", "columns": [
        {"header": "ItemSrNo", "field": "#row"}, {"header": "Desc", "field": "product_description"},
        {"header": "Unit", "field": "unit"}, {"header": "Qty", "field": "quantity"}]},
    {"sheet": "GENERAL", "scope": "job", "columns": [
        {"header": "IGM", "field": "IGM No"}, {"header": "Pkgs", "field": "No of Pkgs"},
        {"header": "Remarks", "field": "Remarks"}, {"header": "Cur", "field": "Currency", "value": "INR"}]},
]}


def _setup(db):
    t = make_tenant(db)
    g = TemplateGroup(tenant_id=t.id, name="Air import", status="approved", entry_mode="excel")
    db.add(g)
    db.commit()
    d = TemplateDocument(tenant_id=t.id, group_id=g.id, name="Invoice")
    db.add(d)
    db.commit()
    db.add(FieldMark(tenant_id=t.id, document_id=d.id, label_name="unit", x=0, y=0, width=1, height=1,
                     fixed_options=[f"{u}:{u.title()}" for u in UNITS]))
    for label in ("IGM No", "No of Pkgs"):
        db.add(CustomField(tenant_id=t.id, group_id=g.id, label_name=label, kind="hardcoded",
                           ask_operator=True, ask_operator_required=True))
    db.add(CustomField(tenant_id=t.id, group_id=g.id, label_name="Remarks", kind="hardcoded"))
    job = Job(tenant_id=t.id, group_id=g.id, reference="JOB-FILL", status="extracted", auto_erp_submitted=True)
    db.add(job)
    db.commit()
    return t, g, job


def test_nearest_option():
    assert nearest_option("pieces", UNITS) == "PCS"
    assert nearest_option("Kg", UNITS) == "KGS"
    assert nearest_option("Nos.", UNITS) == "NOS"
    assert nearest_option("zzz", UNITS) is None


def test_every_line_gets_a_unit_and_required_fields_get_placeholders(db_session):
    _, _, job = _setup(db_session)
    values = {"No of Pkgs": "", "IGM No": "", "Remarks": ""}
    rows = {"product_description": ["A", "B", "C", "D"], "unit": ["", "pieces", "PCS"], "quantity": ["1", "2", "3", "4"]}
    v, r, notes = complete_for_erp(db_session, job, CFG, values, rows)
    assert r["unit"] == ["PCS", "PCS", "PCS", "PCS"]       # line 4 had no value at all
    assert v["IGM No"] == "NA" and v["No of Pkgs"] == "0"  # required, no list: NA / 0
    assert v["Remarks"] == ""                              # not required, no list: left alone
    assert any(n.startswith("unit = PCS") for n in notes)
    assert rows["unit"] == ["", "pieces", "PCS"]           # the caller's data is not changed


def test_empty_everywhere_uses_most_used_on_completed_jobs_then_first(db_session):
    t, g, job = _setup(db_session)
    rows = {"product_description": ["A", "B"], "unit": ["", ""]}
    _, r, notes = complete_for_erp(db_session, job, CFG, {}, rows)
    assert r["unit"] == ["BAG", "BAG"] and "first choice" in notes[0]
    for n in range(3):
        old = Job(tenant_id=t.id, group_id=g.id, reference=f"OLD-{n}", status="completed")
        db_session.add(old)
        db_session.commit()
        db_session.add(JobFieldValue(tenant_id=t.id, job_id=old.id, label_name="unit", row_index=1,
                                     extracted_value="Nos" if n else "BOX"))
    db_session.commit()
    _, r, notes = complete_for_erp(db_session, job, CFG, {}, rows)
    assert r["unit"] == ["NOS", "NOS"] and "most used" in notes[0]


def test_the_quantity_unit_feeds_empty_lines(db_session):
    _, _, job = _setup(db_session)
    _, r, notes = complete_for_erp(db_session, job, CFG, {"Total Quantity Unit": "Sets"},
                                   {"product_description": ["A"], "unit": [""]})
    assert r["unit"] == ["SET"] and "quantity unit" in notes[0]


def test_job_values_on_screen_lines_then_header_quantity_unit(db_session):
    from app.core.auto_erp_complete import fill_job_values

    t, g, job = _setup(db_session)
    d = db_session.query(TemplateDocument).filter(TemplateDocument.group_id == g.id).first()
    db_session.add(FieldMark(tenant_id=t.id, document_id=d.id, label_name="Total Quantity Unit", x=0, y=0,
                             width=1, height=1, fixed_options=UNITS))
    for n in range(2):                                      # past jobs use NOS
        old = Job(tenant_id=t.id, group_id=g.id, reference=f"OLD-{n}", status="completed")
        db_session.add(old)
        db_session.commit()
        db_session.add(JobFieldValue(tenant_id=t.id, job_id=old.id, label_name="unit", row_index=1,
                                     extracted_value="NOS"))
    for r in (1, 2, 3):
        db_session.add(JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="unit", row_index=r, set_index=1,
                                     extracted_value=None))
    db_session.add(JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="Total Quantity Unit",
                                 extracted_value=None))
    db_session.add(JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="IGM No", extracted_value=""))
    db_session.commit()

    notes = fill_job_values(db_session, job)
    vals = {(fv.label_name, fv.row_index): fv for fv in db_session.query(JobFieldValue).filter(JobFieldValue.job_id == job.id)}
    assert [vals[("unit", r)].value for r in (1, 2, 3)] == ["NOS", "NOS", "NOS"]
    assert vals[("Total Quantity Unit", None)].value == "NOS"           # from the lines
    assert vals[("IGM No", None)].value == "NA"
    assert all(fv.corrected_value is None for fv in vals.values())       # never a correction
    assert any("Total Quantity Unit = NOS" in n for n in notes)


def test_job_values_keep_a_persons_choice_and_valid_values(db_session):
    from app.core.auto_erp_complete import fill_job_values

    t, _g, job = _setup(db_session)
    db_session.add(JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="unit", row_index=1, set_index=1,
                                 corrected_value="BAG"))
    db_session.add(JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="unit", row_index=2, set_index=1,
                                 extracted_value="SET"))
    db_session.add(JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="unit", row_index=3, set_index=1,
                                 extracted_value="pieces"))
    db_session.commit()
    fill_job_values(db_session, job)
    got = {fv.row_index: fv.value for fv in db_session.query(JobFieldValue).filter(JobFieldValue.job_id == job.id)}
    assert got == {1: "BAG", 2: "SET", 3: "PCS"}
