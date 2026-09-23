"""Duplicate detection: a job's extracted data is fingerprinted (_compute_content_checksum)
so a re-sent email - a forward, a resend, the same PDF pulled from two mailboxes, each with
its own new Message-ID - is recognised as the same shipment instead of silently becoming a
second job. Flagged jobs get status "possible_duplicate" (never the existing "duplicate",
which means something else entirely: the ERP itself already had this record) and wait for
an operator decision via POST /jobs/{id}/duplicate-decision."""

from app.api.v1.jobs import _compute_content_checksum, _find_duplicate_job
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant, name="Sea Import"):
    group = TemplateGroup(tenant_id=tenant.id, name=name, status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    return group


def _make_job(db_session, tenant, group, status="extracted", checksum=None, reference="JOB-TEST"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status=status,
             content_checksum=checksum)
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


INVOICE_DATA = {"Invoice": {"Invoice No": "INV-001", "Amount": "1000.00"},
                "Packing List": {"Net Weight": "5 KGS"}}


def test_checksum_is_deterministic_regardless_of_key_order():
    a = _compute_content_checksum({"Invoice": {"Invoice No": "INV-1", "Amount": "500"}})
    b = _compute_content_checksum({"Invoice": {"Amount": "500", "Invoice No": "INV-1"}})
    assert a == b
    assert a is not None


def test_checksum_differs_for_different_data():
    a = _compute_content_checksum(INVOICE_DATA)
    b = _compute_content_checksum({"Invoice": {"Invoice No": "INV-002", "Amount": "1000.00"}})
    assert a != b


def test_checksum_is_case_and_whitespace_insensitive():
    a = _compute_content_checksum({"Invoice": {"Invoice No": "  inv-001  "}})
    b = _compute_content_checksum({"Invoice": {"Invoice No": "INV-001"}})
    assert a == b


def test_checksum_handles_line_item_lists():
    a = _compute_content_checksum({"Invoice": {"Part No": ["A1", "A2"]}})
    b = _compute_content_checksum({"Invoice": {"Part No": ["A1", "A2"]}})
    c = _compute_content_checksum({"Invoice": {"Part No": ["A1", "A3"]}})
    assert a == b
    assert a != c


def test_checksum_is_none_when_every_value_is_empty():
    assert _compute_content_checksum({"Invoice": {"Invoice No": "", "Amount": None}}) is None
    assert _compute_content_checksum({}) is None


def test_find_duplicate_job_matches_same_tenant_same_template_same_checksum(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    original = _make_job(db_session, tenant, group, checksum="abc123", reference="JOB-ORIGINAL")
    new_job = _make_job(db_session, tenant, group, checksum="abc123", reference="JOB-NEW",
                        status="extracting")
    found = _find_duplicate_job(db_session, new_job)
    assert found is not None
    assert found.id == original.id


def test_find_duplicate_job_ignores_a_different_checksum(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    _make_job(db_session, tenant, group, checksum="abc123", reference="JOB-ORIGINAL")
    new_job = _make_job(db_session, tenant, group, checksum="xyz999", reference="JOB-NEW")
    assert _find_duplicate_job(db_session, new_job) is None


def test_find_duplicate_job_ignores_a_different_template(db_session):
    tenant = make_tenant(db_session)
    group_a = _make_group(db_session, tenant, name="Sea Import")
    group_b = _make_group(db_session, tenant, name="Air Import")
    _make_job(db_session, tenant, group_a, checksum="abc123", reference="JOB-A")
    new_job = _make_job(db_session, tenant, group_b, checksum="abc123", reference="JOB-B")
    assert _find_duplicate_job(db_session, new_job) is None


def test_find_duplicate_job_ignores_a_different_tenant(db_session):
    tenant_a = make_tenant(db_session, name="Tenant A")
    tenant_b = make_tenant(db_session, name="Tenant B")
    group_a = _make_group(db_session, tenant_a)
    group_b = _make_group(db_session, tenant_b)
    _make_job(db_session, tenant_a, group_a, checksum="abc123", reference="JOB-A")
    new_job = _make_job(db_session, tenant_b, group_b, checksum="abc123", reference="JOB-B")
    assert _find_duplicate_job(db_session, new_job) is None


def test_find_duplicate_job_does_not_chain_off_another_pending_duplicate(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    # A job that is ITSELF still an unresolved possible-duplicate must not be treated as the
    # canonical original - only a settled job counts as "the real one".
    _make_job(db_session, tenant, group, checksum="abc123", status="possible_duplicate",
             reference="JOB-PENDING")
    new_job = _make_job(db_session, tenant, group, checksum="abc123", reference="JOB-NEW")
    assert _find_duplicate_job(db_session, new_job) is None


def test_find_duplicate_job_with_no_checksum_returns_none(db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    _make_job(db_session, tenant, group, checksum="abc123", reference="JOB-ORIGINAL")
    new_job = _make_job(db_session, tenant, group, checksum=None, reference="JOB-NEW")
    assert _find_duplicate_job(db_session, new_job) is None


def test_possible_duplicate_status_maps_to_its_own_stage_and_outer_status(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    original = _make_job(db_session, tenant, group, reference="JOB-ORIGINAL")
    flagged = _make_job(db_session, tenant, group, status="possible_duplicate",
                        reference="JOB-FLAGGED")
    flagged.duplicate_of_job_id = original.id
    db_session.commit()

    admin = make_user(db_session, role="super_admin", email="dup-admin@example.com")
    login(client, admin.email)
    resp = client.get("/api/v1/jobs")
    assert resp.status_code == 200
    row = next(j for j in resp.json() if j["reference"] == "JOB-FLAGGED")
    assert row["stage"] == "Possible Duplicate"
    assert row["outer_status"] == "Possible Duplicate"
    assert row["duplicate_of_reference"] == "JOB-ORIGINAL"


def test_approving_a_possible_duplicate_lets_it_proceed_as_extracted(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    original = _make_job(db_session, tenant, group, reference="JOB-ORIGINAL")
    flagged = _make_job(db_session, tenant, group, status="possible_duplicate",
                        reference="JOB-FLAGGED")
    flagged.duplicate_of_job_id = original.id
    db_session.commit()

    admin = make_user(db_session, role="super_admin", email="dup-admin2@example.com")
    login(client, admin.email)
    resp = client.post(f"/api/v1/jobs/{flagged.id}/duplicate-decision", json={"decision": "approve"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "extracted"
    # The audit trail survives approval - what it WAS flagged against is not erased.
    assert body["duplicate_of_job_id"] == original.id


def test_approving_a_job_that_is_not_flagged_is_rejected(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    job = _make_job(db_session, tenant, group, status="extracted", reference="JOB-NORMAL")

    admin = make_user(db_session, role="super_admin", email="dup-admin3@example.com")
    login(client, admin.email)
    resp = client.post(f"/api/v1/jobs/{job.id}/duplicate-decision", json={"decision": "approve"})
    assert resp.status_code == 400


def test_delete_decision_is_not_handled_by_this_endpoint(client, db_session):
    tenant = make_tenant(db_session)
    group = _make_group(db_session, tenant)
    flagged = _make_job(db_session, tenant, group, status="possible_duplicate",
                        reference="JOB-FLAGGED2")

    admin = make_user(db_session, role="super_admin", email="dup-admin4@example.com")
    login(client, admin.email)
    resp = client.post(f"/api/v1/jobs/{flagged.id}/duplicate-decision", json={"decision": "delete"})
    assert resp.status_code == 400
