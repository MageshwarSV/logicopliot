"""Saving a CTH must never fail because of the "remember it for next time" step.

Found live on JOB-E66A9F / JOB-7B0698 (Air import, November 5): a Juniper line whose product
description is 319 characters. Filling CTH by hand - typed, or by ticking Document CTH - also
remembers "this product -> this code" in the reference table, keyed on that description. The
key column is 255 characters, Postgres refused the row, and since remembering shared the
operator's own transaction, their CTH was thrown away with it: "could not save" when typed, a
tick that never stuck when picked.

Two guards, both pinned here:
  1. Keys are capped at 255 in normalize_key, so the stored key and every later lookup of the
     same long description are cut identically and still find each other.
  2. Remembering runs in a savepoint: if it fails for any reason, only the remembering is
     undone - the operator's value is saved regardless.
"""
from app.core.reference_cache import (
    MAX_KEY_LENGTH,
    lookup_reference,
    normalize_key,
    remember_reference,
)
from app.models.custom_field import CustomField
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN
from tests.conftest import login, make_tenant, make_user

LONG_DESCRIPTION = (
    "TRANSCEIVER(JCO-4C-ZRM-HP-P-B)-JCO400-QDD-ZR-M-HP COHERENT LICENSE (BUNDLE PARENT PART "
    "NUMBER USED FOR ORDERING PURPOSES ONLY) (CONSISTS OF CHILD JCO400-QDD-ZR-M-HP (HARDWARE "
    "ITEM DELIVERED HEREWITH), S-JCO-400-A1-ZRM-P (ITEM SEPARATELY DELIVERED ELECTRONICALLY BY "
    "JUNIPER NETWORKS)) HS 8517620090 ECCN 5A991 NLR"
)


def _setup(client, db_session):
    tenant = make_tenant(db_session)
    make_user(db_session, role=SUPER_ADMIN, email="sa-longdesc@example.com")
    login(client, "sa-longdesc@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="ready")
    db_session.add(group)
    db_session.flush()

    cth = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="CTH",
                      kind="lookup", per_row=True, ask_operator=True,
                      ask_operator_required=True, lookup_key_label="Customer Part code")
    document = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Document CTH",
                           kind="ai", per_row=True, ask_operator=False)
    db_session.add_all([cth, document])
    db_session.flush()
    dump = CustomField(tenant_id=tenant.id, group_id=group.id, label_name="Dump CTH Number",
                       kind="lookup", per_row=True, ask_operator=True,
                       ask_operator_required=True, lookup_key_label="Customer Part code",
                       paired_custom_field_id=document.id, sync_field_ids=[cth.id])
    db_session.add(dump)
    db_session.flush()

    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-LONGDESC", status="draft")
    db_session.add(job)
    db_session.flush()

    fvs = {}
    for cf, extracted in ((dump, ""), (document, "85176290"), (cth, "")):
        fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, custom_field_id=cf.id,
                           label_name=cf.label_name, row_index=1, set_index=1,
                           extracted_value=extracted)
        db_session.add(fv)
        fvs[cf.label_name] = fv
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name="product_description",
                                 row_index=1, set_index=1, extracted_value=LONG_DESCRIPTION))
    db_session.commit()
    return job, cth, dump, fvs


def _value(db_session, fv):
    db_session.refresh(fv)
    return (fv.corrected_value if fv.corrected_value is not None else fv.extracted_value) or ""


def test_normalize_key_never_exceeds_the_column():
    assert len(LONG_DESCRIPTION) > MAX_KEY_LENGTH
    assert len(normalize_key(LONG_DESCRIPTION)) <= MAX_KEY_LENGTH
    assert len(normalize_key("A" * 1000)) == MAX_KEY_LENGTH


def test_a_long_description_is_learned_and_found_again(client, db_session):
    """Capping must not break the point of remembering: the same long line on the next job
    still finds the code."""
    job, cth, _dump, _fvs = _setup(client, db_session)
    remember_reference(db_session, cth.id, ["", LONG_DESCRIPTION], "85176290")
    db_session.commit()
    assert lookup_reference(db_session, cth.id, ["", LONG_DESCRIPTION]) == "85176290"


def test_typing_cth_on_a_long_description_line_saves(client, db_session):
    job, _cth, _dump, fvs = _setup(client, db_session)
    r = client.patch(f"/api/v1/job-field-values/{fvs['CTH'].id}",
                     json={"corrected_value": "85176290"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _value(db_session, fvs["CTH"]) == "85176290"


def test_ticking_document_cth_on_a_long_description_line_saves(client, db_session):
    job, _cth, dump, fvs = _setup(client, db_session)
    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": dump.id, "mode": "pick", "which": "other",
    })
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _value(db_session, fvs["CTH"]) == "85176290"


def test_a_failing_remember_never_loses_the_operators_value(client, db_session, monkeypatch):
    """The safety net itself, for whatever the reference table refuses next."""
    import app.core.reference_cache as reference_cache

    def _boom(*args, **kwargs):
        raise RuntimeError("reference table refused the row")

    monkeypatch.setattr(reference_cache, "remember_reference", _boom)
    job, _cth, dump, fvs = _setup(client, db_session)

    r = client.patch(f"/api/v1/job-field-values/{fvs['CTH'].id}",
                     json={"corrected_value": "85176290"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _value(db_session, fvs["CTH"]) == "85176290"

    client.patch(f"/api/v1/job-field-values/{fvs['CTH'].id}", json={"corrected_value": ""})
    r = client.post(f"/api/v1/jobs/{job.id}/carry-across-rows", json={
        "custom_field_id": dump.id, "mode": "pick", "which": "other",
    })
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert _value(db_session, fvs["CTH"]) == "85176290"
