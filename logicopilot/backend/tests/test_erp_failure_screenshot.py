"""persist_run_outcome used to persist a SUCCESSFUL run's screenshot (via
captured["erp_success_screenshot"], written to disk by browser.py itself) but a FAILED run's
screenshot only ever existed in the single response that fired the run - reload the job
later (exactly what a "Final Approve & Proceed" page showing a stale failure banner
invites) and there was no way to see WHAT the ERP was actually showing when it got stuck,
unlike a successful run, which kept its picture forever. Confirmed live: JOB-290770's own
"Final Approve & Proceed" screen showed only a text reason ("stuck too long...") with
nothing to look at.
"""
import base64

from app.api.v1.jobs import _job_doc_dir, persist_run_outcome
from app.models.job import Job
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant

_TINY_PNG_B64 = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100"
    "ffff03000006000557bfabd40000000049454e44ae426082"
)).decode()


def _make_job(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-SHOT", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job


def test_a_failed_run_persists_its_screenshot_to_disk(db_session):
    job = _make_job(db_session)
    result = {
        "status": "failed", "reason": "The web-entry got stuck too long and could not be completed.",
        "log": ["step 20 upload ok", "ABORTED: stuck too long"], "screenshot": _TINY_PNG_B64,
        "final_url": "https://example-erp.test/screen",
    }
    persist_run_outcome(db_session, job, script=None, result=result)

    entry = job.erp_captured.get("erp_failure_screenshot")
    assert entry is not None
    assert entry["kind"] == "image"
    from app.core.config import get_settings
    from pathlib import Path
    saved = Path(get_settings().uploads_dir) / "jobs" / "_erp_captured" / job.id / entry["file"]
    assert saved.exists()
    assert saved.read_bytes() == base64.b64decode(_TINY_PNG_B64)


def test_a_clean_ok_run_never_gets_a_failure_screenshot(db_session):
    """A genuinely successful run has its OWN screenshot already (written by browser.py) -
    this must never add a second, redundant one just because a screenshot happened to be
    in the result."""
    job = _make_job(db_session)
    result = {"status": "ok", "log": ["ok"], "screenshot": _TINY_PNG_B64,
             "final_url": "https://example-erp.test/done"}
    persist_run_outcome(db_session, job, script=None, result=result)

    assert "erp_failure_screenshot" not in (job.erp_captured or {})


def test_no_screenshot_in_the_result_is_a_silent_no_op(db_session):
    job = _make_job(db_session)
    result = {"status": "failed", "reason": "no ready script", "log": []}
    persist_run_outcome(db_session, job, script=None, result=result)

    assert "erp_failure_screenshot" not in (job.erp_captured or {})


def test_a_later_success_does_not_leave_a_stale_failure_screenshot_entry_behind(db_session):
    """A job that failed once, then succeeds on retry, must not keep showing the OLD
    failure screenshot alongside (or instead of) the new success picture - job.erp_captured
    is fully replaced by each run's own result.captured, and a clean "ok" run's captured
    dict never carries this key to begin with."""
    job = _make_job(db_session)
    persist_run_outcome(db_session, job, script=None, result={
        "status": "failed", "reason": "stuck", "log": [], "screenshot": _TINY_PNG_B64,
    })
    assert "erp_failure_screenshot" in job.erp_captured

    persist_run_outcome(db_session, job, script=None, result={
        "status": "ok", "log": ["ok"],
        "captured": {"bill_of_entry_no": "BE12345"},
    })
    assert "erp_failure_screenshot" not in job.erp_captured
    assert job.erp_captured.get("bill_of_entry_no") == "BE12345"
