"""Jobs list: ERP Branch - the value of the template's ERP script branch field."""
from app.api.v1.jobs import _erp_branch, _job_passes_extra_filters
from app.models.erp_script import ErpScript
from app.models.job import Job, JobFieldValue
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def test_erp_branch_follows_the_script_branch_field(db_session):
    t = make_tenant(db_session)
    g = TemplateGroup(tenant_id=t.id, name="Air import", status="approved")
    other = TemplateGroup(tenant_id=t.id, name="No script", status="approved")
    db_session.add_all([g, other])
    db_session.commit()
    db_session.add(ErpScript(tenant_id=t.id, name="SOFTLINK", url="https://x", status="ready",
                             template_ids=[g.id], branch_field="Destination"))
    j = Job(tenant_id=t.id, group_id=g.id, reference="J1", status="extracted")
    k = Job(tenant_id=t.id, group_id=other.id, reference="J2", status="extracted")
    db_session.add_all([j, k])
    db_session.commit()
    db_session.add(JobFieldValue(tenant_id=t.id, job_id=j.id, label_name="Destination", extracted_value=" Bangalore "))
    db_session.add(JobFieldValue(tenant_id=t.id, job_id=k.id, label_name="Destination", extracted_value="Chennai"))
    db_session.commit()
    assert _erp_branch(db_session, j) == "Bangalore"
    assert _erp_branch(db_session, k) is None              # no ERP script branch field
    kw = dict(assigned_to=None, customer=None, status=None, shipment_type=None)
    assert _job_passes_extra_filters(db_session, j, erp_branch="Bangalore", **kw)
    assert not _job_passes_extra_filters(db_session, j, erp_branch="Chennai", **kw)
