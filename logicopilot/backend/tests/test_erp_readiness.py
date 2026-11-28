"""ERP Field Configuration (R7 + R2) and the readiness-check additions (R1 + R3), R6 report closing."""
import uuid
from datetime import datetime, timezone

from app.core import erp_rules as er
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.job_failure import JobFailureReport
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user

CFG = {"file_name": "icegate.xlsx", "sheets": [
    {"sheet": "GENERAL", "scope": "job", "columns": [
        {"header": "TransportModeCode", "field": "Mode"},
        {"header": "PortOfShipmentCode", "field": "Shipment Port Code"},
        {"header": "BETypeCode", "value": "H"},
        {"header": "IGM_No", "field": "IGM Number"},            # not a field on the template
        {"header": "Remarks"},                                  # nothing feeds it
    ]},
    {"sheet": "SHIPMENT", "scope": "job", "columns": [
        {"header": "PkgUnitCode", "field": "Package Unit"},
    ]},
    {"sheet": "ITEMS", "scope": "line", "columns": [
        {"header": "Unit", "field": "unit"},
    ]},
]}


def _world(db, mode="A", port="CNSHA", pkg="BOX", units=("NOS",), rules=None):
    t = make_tenant(db, name=f"T-{uuid.uuid4().hex[:5]}")
    g = TemplateGroup(tenant_id=t.id, name="Air import", status="approved", entry_mode="excel",
                      excel_config=CFG, erp_column_rules=rules)
    db.add(g)
    db.flush()
    awb = TemplateDocument(tenant_id=t.id, group_id=g.id, name="Air Waybill", doc_type="BL")
    inv = TemplateDocument(tenant_id=t.id, group_id=g.id, name="Invoice", doc_type="Invoice")
    db.add_all([awb, inv])
    db.flush()

    def mark(doc, label, **kw):
        db.add(FieldMark(tenant_id=t.id, document_id=doc.id, label_name=label, x=0, y=0, width=1, height=1, **kw))

    mark(awb, "Shipment Port Code")
    mark(awb, "Package Unit", ask_operator=True, ask_operator_required=True)
    mark(inv, "unit", validation_pattern=r"(?:NOS|PCS|KGS)", validation_message="A unit code: NOS, PCS or KGS")
    db.add(CustomField(tenant_id=t.id, group_id=g.id, label_name="Mode", kind="hardcoded", hardcoded_value=mode))
    db.flush()
    job = Job(tenant_id=t.id, group_id=g.id, reference=f"JOB-{uuid.uuid4().hex[:6]}", status="extracted")
    db.add(job)
    db.flush()
    db.add_all([JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="Mode", extracted_value=mode),
                JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="Shipment Port Code", extracted_value=port),
                JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="Package Unit", extracted_value=pkg)])
    for i, u in enumerate(units, start=1):
        db.add(JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="unit", row_index=i, extracted_value=u))
    db.commit()
    return t, g, job


def _check(db, g, job):
    from app.api.v1.jobs import entry_values_and_rows

    db.refresh(g)
    values, rows = entry_values_and_rows(db, job)
    return er.check(db, job, g, values, rows)


def test_port_code_length_depends_on_transport_mode(db_session):
    _t, g, job = _world(db_session, mode="A", port="CNSHA")
    probs = _check(db_session, g, job)
    assert any(p["column"] == "PortOfShipmentCode" and "3 characters" in p["rule"] for p in probs)
    _t, g, job = _world(db_session, mode="S", port="CNSHA")
    assert not any(p["column"] == "PortOfShipmentCode" for p in _check(db_session, g, job))
    _t, g, job = _world(db_session, mode="A", port="PVG")
    assert not any(p["column"] == "PortOfShipmentCode" for p in _check(db_session, g, job))


def test_allowed_values_rule(db_session):
    rules = {"SHIPMENT|PkgUnitCode": {"allowed": ["PKG", "PLT", "CTN"]}}
    _t, g, job = _world(db_session, mode="S", pkg="BOX", rules=rules)
    p = next(p for p in _check(db_session, g, job) if p["column"] == "PkgUnitCode")
    assert "One of: CTN, PKG, PLT" in p["rule"]
    _t, g, job = _world(db_session, mode="S", pkg="plt", rules=rules)
    assert not any(p["column"] == "PkgUnitCode" for p in _check(db_session, g, job))


def test_field_pattern_now_checked_on_the_server_and_on_product_lines(db_session):
    _t, g, job = _world(db_session, mode="S", units=("NOS", "PC"))
    probs = [p for p in _check(db_session, g, job) if p["column"] == "Unit"]
    assert len(probs) == 1 and probs[0]["product"] == 2 and "NOS, PCS or KGS" in probs[0]["rule"]


def test_config_page_rows_status_and_suggestions(client, db_session):
    _t, g, _job = _world(db_session, mode="S")
    admin = make_user(db_session, role=SUPER_ADMIN, email=f"sa-{uuid.uuid4().hex[:6]}@x.com")
    assert login(client, admin.email).status_code == 200
    body = client.get(f"/api/v1/erp-rules/{g.id}/config").json()
    rows = {r["header"]: r for r in body["rows"]}
    assert body["excel_file"] == "icegate.xlsx"
    assert rows["PortOfShipmentCode"]["document"] == "Air Waybill" and rows["PortOfShipmentCode"]["status"] == "configured"
    assert any("Air" in t and "Sea" in t for t in rows["PortOfShipmentCode"]["rules"])
    assert rows["IGM_No"]["status"] == "invalid" and "not on this template" in rows["IGM_No"]["problems"][0]
    assert rows["Remarks"]["status"] == "not_used"
    assert rows["BETypeCode"]["document"] == "Fixed value"
    assert rows["PkgUnitCode"]["suggest_mandatory"] and rows["PkgUnitCode"]["suggest_strength"] == "strong"
    assert any("Job screen" in t for t in rows["Unit"]["rules"]) and rows["Unit"]["per_line"]

    r = client.post(f"/api/v1/erp-rules/{g.id}/mandatory", json={"keys": ["SHIPMENT|PkgUnitCode", "GENERAL|Remarks"]})
    assert r.json()["changed"] == 2
    rows = {r["header"]: r for r in client.get(f"/api/v1/erp-rules/{g.id}/config").json()["rows"]}
    assert rows["PkgUnitCode"]["mandatory"] is True
    assert rows["Remarks"]["status"] == "missing"          # mandatory with nothing feeding it


def test_analyse_offers_accepted_values(client, db_session):
    _t, g, job = _world(db_session, mode="S", pkg="PKG")
    job.ai_submitted_at = datetime.now(timezone.utc)
    db_session.commit()
    admin = make_user(db_session, role=SUPER_ADMIN, email=f"sa-{uuid.uuid4().hex[:6]}@x.com")
    assert login(client, admin.email).status_code == 200
    assert client.post(f"/api/v1/erp-rules/{g.id}/analyse").json()["jobs"] == 1
    rows = {r["header"]: r for r in client.get(f"/api/v1/erp-rules/{g.id}/config").json()["rows"]}
    assert rows["PkgUnitCode"]["fill_pct"] == 100 and rows["PkgUnitCode"]["accepted_values"] == ["PKG"]
    assert rows["BETypeCode"]["accepted_values"] is None      # a fixed value is never a list
    # saving rules keeps the analysis
    client.put(f"/api/v1/erp-rules/{g.id}", json={"rules": {"SHIPMENT|PkgUnitCode": {"allowed": "pkg, plt"}}})
    db_session.refresh(g)
    assert g.erp_column_rules["SHIPMENT|PkgUnitCode"] == {"allowed": ["PKG", "PLT"]}
    assert "_analysis" in g.erp_column_rules


def test_failure_reports_resolved_when_job_enters_erp(db_session):
    t, _g, job = _world(db_session)
    db_session.add(JobFailureReport(tenant_id=t.id, job_id=job.id, job_reference=job.reference, reason="stuck", status="open"))
    db_session.commit()
    job.ai_submitted_at = datetime.now(timezone.utc)
    db_session.commit()
    rep = db_session.query(JobFailureReport).filter_by(job_id=job.id).one()
    db_session.refresh(rep)
    assert rep.status == "resolved" and rep.reason == "stuck"
