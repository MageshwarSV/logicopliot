"""Indian Customs Tariff on jobs - phase 3 (human review): after-extraction pass, save check,
submit check, carry-across. The AI is replaced by a stub."""
import pytest

from app.api.v1.jobs import _unanswered_operator_fields
from app.core import customs_tariff as ct
from app.core import customs_tariff_jobs as tj
from app.core import customs_tariff_match as m
from app.models.custom_field import CustomField
from app.models.customs_tariff import CustomsTariff, CustomsTariffUpload
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user

CODES = ["85371010", "85371090", "85177910", "85177990", "39232100", "39232990"]


def _setup(db, on=True):
    up = CustomsTariffUpload(file_name="t.xlsx")
    db.add(up)
    db.flush()
    for c in CODES:
        db.add(CustomsTariff(cth=c, is_active=True, upload_id=up.id))
    t = make_tenant(db)
    g = TemplateGroup(tenant_id=t.id, name="Sea import", status="approved", use_customs_tariff=on)
    db.add(g)
    db.commit()
    doc = CustomField(tenant_id=t.id, group_id=g.id, label_name="Document CTH", kind="ai", per_row=True)
    cth = CustomField(tenant_id=t.id, group_id=g.id, label_name="CTH", kind="lookup", per_row=True,
                      ask_operator=True, ask_operator_required=False, lookup_key_label="Customer Part code")
    db.add_all([doc, cth])
    db.flush()
    dump = CustomField(tenant_id=t.id, group_id=g.id, label_name="Dump CTH Number", kind="lookup", per_row=True,
                       ask_operator=True, ask_operator_required=False, lookup_key_label="Customer Part code",
                       paired_custom_field_id=doc.id, sync_field_ids=[cth.id])
    db.add(dump)
    db.flush()
    job = Job(tenant_id=t.id, group_id=g.id, reference="JOB-T1", status="extracted")
    db.add(job)
    db.flush()
    # line 1: dump code in the tariff; line 2: dump code NOT in it; line 3: nothing in the dump
    lines = {1: ("85177990", "", "Router board"), 2: ("85371000", "8537.10.00", "Patch panel cover"),
             3: ("", "", "Plastic bag")}
    for row, (dump_val, doc_val, desc) in lines.items():
        db.add_all([
            JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="product_description", row_index=row, extracted_value=desc),
            JobFieldValue(tenant_id=t.id, job_id=job.id, label_name="Customer Part code", row_index=row, extracted_value=f"P{row}"),
            JobFieldValue(tenant_id=t.id, job_id=job.id, custom_field_id=doc.id, label_name="Document CTH", row_index=row, extracted_value=doc_val),
            JobFieldValue(tenant_id=t.id, job_id=job.id, custom_field_id=dump.id, label_name="Dump CTH Number", row_index=row, extracted_value=dump_val),
            JobFieldValue(tenant_id=t.id, job_id=job.id, custom_field_id=cth.id, label_name="CTH", row_index=row, extracted_value=dump_val),
        ])
    db.commit()
    ct._ACTIVE.clear()
    return t, g, job, dump, cth


def _fv(db, job, label, row):
    return db.query(JobFieldValue).filter_by(job_id=job.id, label_name=label, row_index=row).one()


class FakeAI:
    def __call__(self, system, user, tenant_id, n):
        if system == m._GUESS_SYSTEM:
            return {i: {"n": i, "cth": "39232199"} for i in range(1, n + 1)}
        out = {}
        for block in user.split("\n"):
            if block.startswith("Line "):
                n_ = int(block.split(":")[0].split()[1])
                out[n_] = {"n": n_, "cth": "85371090" if "Patch" in block else "39232100", "confidence": "medium"}
        return out


@pytest.fixture()
def fake_ai(monkeypatch):
    monkeypatch.setattr(m, "_ask", FakeAI())


def test_pass_keeps_rejects_and_suggests(db_session, fake_ai):
    t, g, job, dump, cth = _setup(db_session)
    stats = tj.apply_to_job(db_session, job, g)
    db_session.commit()
    assert stats == {"kept": 2, "rejected": 2, "suggested": 2}
    # line 1: in the tariff - untouched
    assert _fv(db_session, job, "Dump CTH Number", 1).extracted_value == "85177990"
    assert _fv(db_session, job, "Dump CTH Number", 1).value_source is None
    # line 2: dump 85371000 not in the tariff - emptied; the dump box shows only the dump
    d2 = _fv(db_session, job, "Dump CTH Number", 2)
    assert (d2.extracted_value, d2.value_source) == ("", None)
    assert "Dump code 85371000 is not in the Indian Customs Tariff" in d2.source_note
    # ...and the AI's pick under 853710 goes in the CTH answer box itself (4S, 2026-11-26)
    c2 = _fv(db_session, job, "CTH", 2)
    assert (c2.extracted_value, c2.value_source) == ("85371090", "tariff_ai")
    # line 3: nothing in the dump, no document code - guess then pick, into CTH
    assert _fv(db_session, job, "CTH", 3).extracted_value == "39232100"
    assert _fv(db_session, job, "Dump CTH Number", 3).extracted_value == ""


def test_switch_off_changes_nothing(db_session, fake_ai):
    t, g, job, dump, cth = _setup(db_session, on=False)
    assert tj.apply_to_job(db_session, job, g) == {}
    assert _fv(db_session, job, "Dump CTH Number", 2).extracted_value == "85371000"
    assert tj.submit_problems(db_session, job) == []


def test_submit_takes_the_suggestion_and_blocks_codes_outside_tariff(db_session, fake_ai):
    t, g, job, dump, cth = _setup(db_session)
    tj.apply_to_job(db_session, job, g)
    db_session.commit()
    # the suggestion already sits in CTH - nothing to tick before submitting
    problems = _unanswered_operator_fields(db_session, job)
    assert not any("confirm the suggested tariff code" in p for p in problems)
    assert tj.submit_problems(db_session, job) == []
    # a code outside the tariff (e.g. restored from before the switch) blocks
    _fv(db_session, job, "CTH", 1).corrected_value = "85179090"
    db_session.commit()
    assert tj.submit_problems(db_session, job) == ["CTH (line 1): 85179090 is not in the Indian Customs Tariff"]


def test_save_and_carry_refuse_codes_outside_tariff(client, db_session, fake_ai):
    t, g, job, dump, cth = _setup(db_session)
    tj.apply_to_job(db_session, job, g)
    db_session.commit()
    make_user(db_session, role=TENANT_ADMIN, tenant=t)
    op = make_user(db_session, role=OPERATOR, tenant=t)
    login(client, op.email)
    c1 = _fv(db_session, job, "CTH", 1)
    r = client.patch(f"/api/v1/job-field-values/{c1.id}", json={"corrected_value": "85179090"})
    assert r.status_code == 422 and "not in the Indian Customs Tariff" in r.json()["detail"]
    r = client.patch(f"/api/v1/job-field-values/{c1.id}", json={"corrected_value": "8537 10 10"})
    assert r.status_code == 200 and r.json()["corrected_value"] == "85371010"
    # typed value carried to every line: refused when outside the tariff
    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows",
                    json={"custom_field_id": dump.id, "mode": "value", "value": "12345678", "previous": ""})
    assert r.status_code == 422
    # ticking the Document side: line 2's document code 85371000 is not in the tariff -> skipped
    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows",
                    json={"custom_field_id": dump.id, "mode": "pick", "which": "other"})
    assert r.json()["not_in_tariff_lines"] == [2]
    db_session.expire_all()
    assert tj.submit_problems(db_session, job) == []


def test_detail_carries_source(client, db_session, fake_ai):
    t, g, job, dump, cth = _setup(db_session)
    tj.apply_to_job(db_session, job, g)
    db_session.commit()
    make_user(db_session, role=TENANT_ADMIN, tenant=t)
    op = make_user(db_session, role=OPERATOR, tenant=t)
    login(client, op.email)
    d = client.get(f"/api/v1/jobs/{job.id}").json()
    assert d["customs_tariff_on"] is True
    dump2 = next(f for f in d["field_values"] if f["label_name"] == "Dump CTH Number" and f["row_index"] == 2)
    assert "85371000" in dump2["source_note"]
    cth2 = next(f for f in d["field_values"] if f["label_name"] == "CTH" and f["row_index"] == 2)
    assert cth2["value_source"] == "tariff_ai"


# ---------------------------------------------------------------- phase 4: Auto ERP

def test_auto_apply_fills_the_answer_box_and_notes(db_session, fake_ai):
    t, g, job, dump, cth = _setup(db_session)
    tj.apply_to_job(db_session, job, g)
    db_session.commit()
    notes = tj.auto_apply(db_session, job)
    db_session.commit()
    # the answer box the ERP reads gets each line's tariff code - as extracted, not a correction
    assert [_fv(db_session, job, "CTH", r).value for r in (1, 2, 3)] == ["85177990", "85371090", "39232100"]
    assert all(_fv(db_session, job, "CTH", r).corrected_value is None for r in (1, 2, 3))
    assert _fv(db_session, job, "CTH", 2).value_source == "tariff_ai"
    text = " | ".join(notes)
    assert "AI's nearest tariff code on lines 2, 3" in text and "check in the ERP" in text
    assert "Dump CTH not in the Indian Customs Tariff, not used, on line 2" in text


def test_auto_apply_off_and_no_code(db_session, monkeypatch):
    t, g, job, dump, cth = _setup(db_session, on=False)
    assert tj.auto_apply(db_session, job) == []
    g.use_customs_tariff = True
    db_session.commit()
    monkeypatch.setattr(m, "_ask", lambda *a: {})        # AI down: line 2 has a document code, line 3 nothing
    tj.apply_to_job(db_session, job, g)
    db_session.commit()
    notes = tj.auto_apply(db_session, job)
    assert _fv(db_session, job, "CTH", 2).value == "85371090"     # 'Other' code of 853710
    assert _fv(db_session, job, "CTH", 3).value == ""             # nothing to place it by
    text = " | ".join(notes)
    assert "'Other' code on line 2" in text and "No tariff CTH could be found on line 3" in text


def test_erp_columns_never_carry_na_or_codes_outside_the_tariff(db_session, fake_ai):
    from app.core.auto_erp_complete import complete_for_erp

    t, g, job, dump, cth = _setup(db_session)
    cth.ask_operator_required = True                       # required: would get "NA" without the guard
    db_session.commit()
    cfg = {"sheets": [{"sheet": "ITEMS", "scope": "line", "columns": [
        {"header": "Desc", "field": "product_description"}, {"header": "CTH", "field": "CTH"}]}]}
    rows = {"product_description": ["A", "B", "C"], "CTH": ["85177990", "", "8537.10.00"]}
    _v, r, notes = complete_for_erp(db_session, job, cfg, {}, rows)
    assert r["CTH"] == ["85177990", "", ""]
    assert any("removed - not in the Indian Customs Tariff" in n for n in notes)
