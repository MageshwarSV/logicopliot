"""AI-aided field (4S, 2026-11-27): a box or field with a condition prompt - whatever the operator
types and saves is rewritten by that condition before it is stored ("santos" -> "Santos (BRSSZ)"),
so the screen shows exactly what the Excel carries; the Excel column takes the code in brackets.
"""
import pytest

from app.core import excel_entry as xe
from app.core import llm
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user

CONDITION = "Port Name (UN/LOCODE) - the port's name in title case, then its 5-letter UN/LOCODE in brackets."


@pytest.fixture()
def fake_ai(monkeypatch):
    seen = []

    def convert(value, condition, field_name="", tenant_id=None):
        seen.append((value, condition))
        return {"santos": "Santos (BRSSZ)", "germany": "GERMANY (DE)"}.get(value.lower(), value)

    monkeypatch.setattr(llm, "ai_aided_convert", convert)
    return seen


def _job(db, *, aided: bool):
    t = make_tenant(db)
    g = TemplateGroup(tenant_id=t.id, name="Sea Export", status="approved")
    db.add(g); db.flush()
    d = TemplateDocument(tenant_id=t.id, group_id=g.id, name="INVOICE", doc_type="Invoice")
    db.add(d); db.flush()
    m = FieldMark(tenant_id=t.id, document_id=d.id, label_name="PORT OF DISCHARGE", page_number=1,
                  x=0.1, y=0.1, width=0.2, height=0.05, ai_aided_prompt=CONDITION if aided else None)
    cf = CustomField(tenant_id=t.id, group_id=g.id, label_name="Discharge Country", kind="hardcoded",
                     ask_operator=True, ai_aided_prompt="COUNTRY NAME (ISO2)" if aided else None)
    db.add_all([m, cf]); db.flush()
    j = Job(tenant_id=t.id, group_id=g.id, reference="JOB-AIA", status="extracted")
    db.add(j); db.flush()
    pod = JobFieldValue(tenant_id=t.id, job_id=j.id, mark_id=m.id, template_document_id=d.id,
                        label_name="PORT OF DISCHARGE", extracted_value="Paranagua (BRPNG)")
    cty = JobFieldValue(tenant_id=t.id, job_id=j.id, custom_field_id=cf.id, label_name="Discharge Country",
                        extracted_value="BRAZIL (BR)")
    db.add_all([pod, cty]); db.commit()
    make_user(db, role=TENANT_ADMIN, tenant=t)
    op = make_user(db, role=OPERATOR, tenant=t)
    return op, pod, cty


def test_a_typed_value_is_converted_by_the_condition(client, db_session, fake_ai):
    op, pod, cty = _job(db_session, aided=True)
    login(client, op.email)
    r = client.patch(f"/api/v1/job-field-values/{pod.id}", json={"corrected_value": "santos"})
    assert r.status_code == 200 and r.json()["value"] == "Santos (BRSSZ)"
    r = client.patch(f"/api/v1/job-field-values/{cty.id}", json={"corrected_value": "germany"})
    assert r.status_code == 200 and r.json()["value"] == "GERMANY (DE)"
    assert fake_ai[0] == ("santos", CONDITION)


def test_an_ordinary_field_is_saved_as_typed(client, db_session, fake_ai):
    op, pod, _ = _job(db_session, aided=False)
    login(client, op.email)
    r = client.patch(f"/api/v1/job-field-values/{pod.id}", json={"corrected_value": "santos"})
    assert r.json()["value"] == "santos"
    assert fake_ai == []


def test_the_excel_takes_the_code_in_brackets():
    assert xe._code_in_brackets("Paranagua (BRPNG)") == "BRPNG"
    assert xe._code_in_brackets("BRAZIL (BR)") == "BR"
    assert xe._code_in_brackets("Santos") == "Santos"            # nothing in brackets: as it is
    assert xe._code_in_brackets("") == ""
    assert "code_in_brackets" in xe._TRANSFORMS


def test_the_setting_is_saved_on_create_and_edit(client, db_session):
    from app.models.user import SUPER_ADMIN

    t = make_tenant(db_session)
    g = TemplateGroup(tenant_id=t.id, name="Sea Export", status="draft")
    db_session.add(g); db_session.commit()
    sa = make_user(db_session, role=SUPER_ADMIN)
    login(client, sa.email)
    r = client.post(f"/api/v1/template-groups/{g.id}/custom-fields",
                    json={"label_name": "Destination Country", "kind": "hardcoded", "ask_operator": True,
                          "ai_aided_prompt": "  COUNTRY NAME (ISO2)  "})
    assert r.status_code == 201, r.text
    cf = db_session.get(CustomField, r.json()["id"])
    assert cf.ai_aided_prompt == "COUNTRY NAME (ISO2)"
    r = client.patch(f"/api/v1/custom-fields/{cf.id}", json={"ai_aided_prompt": None})
    assert r.status_code == 200 and r.json()["ai_aided_prompt"] is None
