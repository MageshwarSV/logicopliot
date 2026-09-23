"""GET /system-settings/spend-analytics — platform-wide jobs/documents/pages/estimated-token
report for Super Admin's new Settings > Spendings screen."""
from datetime import datetime, timedelta, timezone

from app.models.job import Job, JobDocument
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user


def _make_group(db_session, tenant):
    """Returns (group, template_document_id) — JobDocument.template_document_id is NOT NULL,
    so every test document needs a real one even though this endpoint doesn't care which."""
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    tdoc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add(tdoc)
    db_session.commit()
    db_session.refresh(tdoc)
    return group, tdoc.id


def _make_job(db_session, tenant, group, *, created_at, reference="JOB-SPEND"):
    job = Job(tenant_id=tenant.id, group_id=group.id, reference=reference, status="extracted")
    db_session.add(job)
    db_session.commit()
    job.created_at = created_at
    db_session.commit()
    db_session.refresh(job)
    return job


def _make_doc(db_session, tenant, job, tdoc_id, *, page_count=3, text="A" * 40, file_path="fake.pdf"):
    doc = JobDocument(
        tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc_id,
        file_path=file_path, page_count=page_count,
        extracted_json={"text": text} if text is not None else None,
    )
    db_session.add(doc)
    db_session.commit()
    return doc


def test_non_super_admin_forbidden(client, db_session):
    tenant = make_tenant(db_session)
    ta = make_user(db_session, role="tenant_admin", tenant=tenant, email="ta-spend1@example.com")
    login(client, ta.email)

    resp = client.get("/api/v1/system-settings/spend-analytics")
    assert resp.status_code == 403


def test_empty_window_returns_zeros(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-spend1@example.com")
    login(client, sa.email)

    resp = client.get("/api/v1/system-settings/spend-analytics",
                      params={"start": "2020-01-01", "end": "2020-01-02"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"] == {
        "jobs": 0, "documents": 0, "pages": 0, "characters": 0, "estimated_tokens": 0,
        "zero_result_jobs": 0,
    }
    assert body["days"] == []


def test_jobs_and_documents_bucketed_by_day_with_correct_math(client, db_session):
    tenant = make_tenant(db_session)
    group, tdoc_id = _make_group(db_session, tenant)
    day1 = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    day2 = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)

    j1 = _make_job(db_session, tenant, group, created_at=day1, reference="JOB-D1")
    _make_doc(db_session, tenant, j1, tdoc_id, page_count=2, text="X" * 40)  # 40 chars -> 10 tokens
    _make_doc(db_session, tenant, j1, tdoc_id, page_count=3, text="Y" * 20)  # 20 chars -> 5 tokens

    j2 = _make_job(db_session, tenant, group, created_at=day2, reference="JOB-D2")
    _make_doc(db_session, tenant, j2, tdoc_id, page_count=1, text="Z" * 100)  # 100 chars -> 25 tokens

    sa = make_user(db_session, role="super_admin", email="sa-spend2@example.com")
    login(client, sa.email)

    resp = client.get("/api/v1/system-settings/spend-analytics",
                      params={"start": "2026-09-08", "end": "2026-09-09"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["totals"] == {
        "jobs": 2, "documents": 3, "pages": 6, "characters": 160, "estimated_tokens": 40,
        "zero_result_jobs": 2,  # neither job has any JobFieldValue at all in this test
    }
    days = {d["date"]: d for d in body["days"]}
    assert days["2026-09-08"] == {
        "date": "2026-09-08", "jobs": 1, "documents": 2, "pages": 5, "characters": 60,
        "estimated_tokens": 15, "zero_result_jobs": 1,
    }
    assert days["2026-09-09"] == {
        "date": "2026-09-09", "jobs": 1, "documents": 1, "pages": 1, "characters": 100,
        "estimated_tokens": 25, "zero_result_jobs": 1,
    }


def test_jobs_outside_range_excluded(client, db_session):
    tenant = make_tenant(db_session)
    group, _ = _make_group(db_session, tenant)
    _make_job(db_session, tenant, group, created_at=datetime(2026, 1, 1, tzinfo=timezone.utc))

    sa = make_user(db_session, role="super_admin", email="sa-spend3@example.com")
    login(client, sa.email)

    resp = client.get("/api/v1/system-settings/spend-analytics",
                      params={"start": "2026-09-08", "end": "2026-09-10"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["totals"]["jobs"] == 0


def test_platform_wide_across_multiple_tenants(client, db_session):
    tenant_a = make_tenant(db_session, name="A")
    tenant_b = make_tenant(db_session, name="B")
    group_a, _ = _make_group(db_session, tenant_a)
    group_b, _ = _make_group(db_session, tenant_b)
    day = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    _make_job(db_session, tenant_a, group_a, created_at=day, reference="A-JOB")
    _make_job(db_session, tenant_b, group_b, created_at=day, reference="B-JOB")

    sa = make_user(db_session, role="super_admin", email="sa-spend4@example.com")
    login(client, sa.email)

    resp = client.get("/api/v1/system-settings/spend-analytics",
                      params={"start": "2026-09-08", "end": "2026-09-08"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["totals"]["jobs"] == 2


def test_default_range_is_last_30_days_ending_today(client, db_session):
    tenant = make_tenant(db_session)
    group, _ = _make_group(db_session, tenant)
    today = datetime.now(timezone.utc)
    _make_job(db_session, tenant, group, created_at=today, reference="JOB-TODAY")
    _make_job(db_session, tenant, group, created_at=today - timedelta(days=40), reference="JOB-OLD")

    sa = make_user(db_session, role="super_admin", email="sa-spend5@example.com")
    login(client, sa.email)

    resp = client.get("/api/v1/system-settings/spend-analytics")
    assert resp.status_code == 200, resp.text
    assert resp.json()["totals"]["jobs"] == 1


def test_invalid_date_rejected(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-spend6@example.com")
    login(client, sa.email)

    resp = client.get("/api/v1/system-settings/spend-analytics", params={"start": "not-a-date"})
    assert resp.status_code == 400


def test_start_after_end_rejected(client, db_session):
    sa = make_user(db_session, role="super_admin", email="sa-spend7@example.com")
    login(client, sa.email)

    resp = client.get("/api/v1/system-settings/spend-analytics",
                      params={"start": "2026-09-10", "end": "2026-09-08"})
    assert resp.status_code == 400


def test_zero_result_jobs_distinguishes_jobs_with_and_without_real_data(client, db_session):
    from app.models.job import JobFieldValue

    tenant = make_tenant(db_session)
    group, tdoc_id = _make_group(db_session, tenant)
    day = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)

    good_job = _make_job(db_session, tenant, group, created_at=day, reference="JOB-GOOD")
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=good_job.id,
                                 label_name="invoice_no", extracted_value="INV-123"))
    empty_job = _make_job(db_session, tenant, group, created_at=day, reference="JOB-EMPTY")
    db_session.add(JobFieldValue(tenant_id=tenant.id, job_id=empty_job.id,
                                 label_name="invoice_no", extracted_value=""))
    no_values_job = _make_job(db_session, tenant, group, created_at=day, reference="JOB-NONE")
    db_session.commit()

    sa = make_user(db_session, role="super_admin", email="sa-spend9@example.com")
    login(client, sa.email)

    resp = client.get("/api/v1/system-settings/spend-analytics",
                      params={"start": "2026-09-08", "end": "2026-09-08"})
    body = resp.json()
    assert body["totals"]["jobs"] == 3
    assert body["totals"]["zero_result_jobs"] == 2  # empty_job and no_values_job
    assert no_values_job.id != good_job.id  # sanity: three distinct jobs were actually created


def test_document_with_no_file_path_excluded(client, db_session):
    """An empty upload slot (no file yet) must not count as a document or contribute pages."""
    tenant = make_tenant(db_session)
    group, tdoc_id = _make_group(db_session, tenant)
    day = datetime(2026, 9, 8, 10, 0, tzinfo=timezone.utc)
    job = _make_job(db_session, tenant, group, created_at=day)
    _make_doc(db_session, tenant, job, tdoc_id, page_count=5, text="hello", file_path=None)

    sa = make_user(db_session, role="super_admin", email="sa-spend8@example.com")
    login(client, sa.email)

    resp = client.get("/api/v1/system-settings/spend-analytics",
                      params={"start": "2026-09-08", "end": "2026-09-08"})
    body = resp.json()
    assert body["totals"]["jobs"] == 1
    assert body["totals"]["documents"] == 0
    assert body["totals"]["pages"] == 0
