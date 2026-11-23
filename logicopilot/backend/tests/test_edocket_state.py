"""E-Docket choices are saved on the job and read back (Print pages prints the same docket)."""
from types import SimpleNamespace

from app.api.v1 import edocket
from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.user import OPERATOR
from tests.conftest import make_tenant, make_user


def test_save_and_read_back(db_session, monkeypatch):
    tenant = make_tenant(db_session)
    g = TemplateGroup(tenant_id=tenant.id, name="Sea", status="approved")
    db_session.add(g)
    db_session.commit()
    job = Job(tenant_id=tenant.id, group_id=g.id, reference="JOB-ED", status="extracted")
    db_session.add(job)
    db_session.commit()
    op = make_user(db_session, role=OPERATOR, tenant=tenant, email="op-ed@example.com", full_name="Nivetha")
    monkeypatch.setattr("app.api.v1.jobs._load_job", lambda db, jid, scope, user=None: db.get(Job, jid))
    assert edocket.get_edocket(job.id, db_session, None, op)["state"] is None
    state = {"transactionMode": "Transaction", "fclLcl": "LCL", "categories": ["HOME CONSUMPTION"],
             "values": {"Weight (Gross)": "2041.000 KG"}}
    saved = edocket.save_edocket(job.id, edocket.EDocketState(state=state), db_session, None, op)
    assert saved["saved_by"] == "Nivetha"
    assert edocket.get_edocket(job.id, db_session, None, op)["state"] == state
