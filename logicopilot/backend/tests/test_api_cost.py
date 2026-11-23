"""API Cost & Usage: one record per day and project (a second run updates it), page figures from
the billing ledger, provisional -> final, failed days kept visible, scheduler times in IST."""
from datetime import date, datetime, timedelta, timezone

import pytest

from app.core import api_cost
from app.models.api_cost import TOTAL_PROJECT, ApiCostDaily, ApiCostDailyCustomer, ApiCostRun
from app.models.billable_page_event import BillablePageEvent
from app.models.job import Job, JobDocument, UnmatchedUploadPage
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant

DAY = date(2026, 11, 20)


def _at(hour):
    return datetime(2026, 11, 20, hour, tzinfo=timezone.utc)


@pytest.fixture
def data(db_session):
    t1, t2 = make_tenant(db_session, name="4S Logistics"), make_tenant(db_session, name="Bhuvaneswari")
    g1 = TemplateGroup(tenant_id=t1.id, name="Air", status="approved")
    g2 = TemplateGroup(tenant_id=t2.id, name="Sea", status="approved")
    db_session.add_all([g1, g2])
    db_session.commit()
    j1 = Job(tenant_id=t1.id, group_id=g1.id, reference="JOB-A", status="extracted")
    j2 = Job(tenant_id=t2.id, group_id=g2.id, reference="JOB-B", status="extracted")
    td = TemplateDocument(tenant_id=t1.id, group_id=g1.id, name="Invoice", doc_type="Invoice")
    db_session.add_all([j1, j2, td])
    db_session.commit()
    db_session.add_all([
        JobDocument(tenant_id=t1.id, job_id=j1.id, template_document_id=td.id, file_path="a.pdf", page_count=8),
        UnmatchedUploadPage(tenant_id=t1.id, job_id=j1.id, original_filename="a.pdf", page_number=9,
                            image_path="x.png"),
        BillablePageEvent(tenant_id=t1.id, job_id=j1.id, job_reference="JOB-A", shipment_type="Air Import",
                          page_count=9, created_at=_at(10)),
        BillablePageEvent(tenant_id=t1.id, job_id=j1.id, job_reference="JOB-A", shipment_type="Air Import",
                          page_count=9, is_rerun=True, created_at=_at(12)),
        BillablePageEvent(tenant_id=t2.id, job_id="deleted-job", job_reference="JOB-GONE", shipment_type="Sea Import",
                          page_count=2, created_at=_at(15)),
        BillablePageEvent(tenant_id=t2.id, job_id=j2.id, job_reference="JOB-B", shipment_type="Sea Import",
                          page_count=5, created_at=_at(1) - timedelta(days=1)),   # another day
    ])
    db_session.commit()
    return t1, t2


def _fake_openai(monkeypatch, usd=(0.30, 0.12)):
    monkeypatch.setattr(api_cost, "openai_day", lambda key, day: {
        "p1": {"name": "cargora", "usd": usd[0], "input_tokens": 1000, "output_tokens": 200, "requests": 10},
        "p2": {"name": "cargora 1", "usd": usd[1], "input_tokens": 500, "output_tokens": 100, "requests": 4}})
    monkeypatch.setattr(api_cost, "usd_inr_rate", lambda day, db=None: (84.0, "test"))


def test_page_stats_by_customer_and_day(db_session, data):
    t1, t2 = data
    total, per = api_cost.page_stats(db_session, DAY)
    assert total == {"pages_billed": 20, "original_pages": 11, "rerun_pages": 9,
                     "classified_pages": 8, "unclassified_pages": 1, "total_jobs": 2}
    assert per[t1.id]["pages_billed"] == 18 and per[t2.id]["pages_billed"] == 2   # deleted job still billed


def test_record_day_and_second_run_updates_not_duplicates(db_session, data, monkeypatch):
    _fake_openai(monkeypatch)
    api_cost.record_day(db_session, DAY, "daily", "key")
    _fake_openai(monkeypatch, usd=(0.35, 0.12))
    api_cost.record_day(db_session, DAY, "daily", "key")
    rows = db_session.query(ApiCostDaily).filter(ApiCostDaily.usage_date == DAY).all()
    assert len(rows) == 3                                            # 2 projects + account total
    total = next(r for r in rows if r.project_id == TOTAL_PROJECT)
    assert total.cost_usd == pytest.approx(0.47) and total.cost_inr == pytest.approx(0.47 * 84)
    assert total.pages_billed == 20 and total.cost_per_page_usd == pytest.approx(0.47 / 20)
    assert total.status == "provisional" and total.input_tokens == 1500 and total.requests == 14
    assert db_session.query(ApiCostDailyCustomer).filter(ApiCostDailyCustomer.usage_date == DAY).count() == 2


def test_reconcile_finalises_a_settled_day(db_session, data, monkeypatch):
    _fake_openai(monkeypatch)
    api_cost.record_day(db_session, DAY, "daily", "key")
    api_cost.record_day(db_session, DAY, "reconcile", "key")         # long finished, unchanged
    total = db_session.query(ApiCostDaily).filter_by(usage_date=DAY, project_id=TOTAL_PROJECT).one()
    assert total.status == "final" and total.reconciled_at is not None


def test_status_rules():
    today = datetime.now(timezone.utc).date()
    assert api_cost._status(today, None, 1.0, "daily") == "provisional"
    assert api_cost._status(today, 1.0, 1.0, "reconcile") == "provisional"            # day not over
    assert api_cost._status(today - timedelta(days=1), 1.0, 1.2, "reconcile") in ("provisional", "final")
    assert api_cost._status(today - timedelta(days=3), None, 1.0, "reconcile") == "final"


def test_failed_run_is_logged_and_the_day_shows_failed(db_session, session_factory, monkeypatch):
    monkeypatch.setattr("app.db.session.SessionLocal", session_factory)
    monkeypatch.setattr("app.core.system_settings.get_openai_admin_key", lambda db: "key")

    def boom(key, day):
        raise RuntimeError("OpenAI down")

    monkeypatch.setattr(api_cost, "openai_day", boom)
    out = api_cost.run([DAY], "daily", "test")
    assert out["status"] == "failed"
    db_session.expire_all()
    assert db_session.query(ApiCostRun).one().status == "failed"
    assert db_session.query(ApiCostDaily).filter_by(usage_date=DAY, project_id=TOTAL_PROJECT).one().status == "failed"


def test_scheduler_times_are_ist(monkeypatch):
    started = []
    monkeypatch.setattr(api_cost, "_claim", lambda key: True)
    monkeypatch.setattr(api_cost, "run", lambda days, kind, by=None: started.append((kind, days)))
    # 23:56 IST on 20 Nov = 18:26 UTC on 20 Nov -> collect OpenAI day 20 Nov
    assert api_cost.tick(datetime(2026, 11, 20, 18, 26, tzinfo=timezone.utc)) == "daily"
    assert started[-1] == ("daily", [date(2026, 11, 20)])
    # 11:05 IST on 21 Nov -> reconcile 18, 19, 20 Nov
    assert api_cost.tick(datetime(2026, 11, 21, 5, 35, tzinfo=timezone.utc)) == "reconcile"
    assert started[-1] == ("reconcile", [date(2026, 11, 18), date(2026, 11, 19), date(2026, 11, 20)])
    # 09:00 IST - nothing yet
    assert api_cost.tick(datetime(2026, 11, 21, 3, 30, tzinfo=timezone.utc)) is None
