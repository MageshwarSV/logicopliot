"""GET /jobs/{job_id}/erp-debug-shots — the screen an AI takeover decided from, for every
AI-driven click a run had to make (see browser.py's ai_takeover). Diagnostic only, never
shown to an operator: found needed live on JOB-BCE250, where the text log alone could not
say what screen the AI actually guessed on when it clicked "Save & Close" on what turned out
to be an empty organization sub-form the recorded script never expected."""
from pathlib import Path

from app.core.config import get_settings
from app.models.job import Job
from app.models.template_group import TemplateGroup
from app.models.user import SUPER_ADMIN, TENANT_ADMIN
from tests.conftest import login, make_tenant, make_user

_TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000100"
    "ffff03000006000557bfabd40000000049454e44ae426082"
)


def _make_job(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="approved")
    db_session.add(group)
    db_session.commit()
    db_session.refresh(group)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-SHOTS", status="extracted")
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)
    return job, tenant


def test_lists_every_captured_ai_takeover_screenshot_as_a_data_url(client, db_session):
    job, _tenant = _make_job(db_session)
    shot_dir = Path(get_settings().uploads_dir) / "jobs" / "_erp_captured" / job.id
    shot_dir.mkdir(parents=True, exist_ok=True)
    (shot_dir / "ai-takeover-1.png").write_bytes(_TINY_PNG)
    (shot_dir / "ai-takeover-2.png").write_bytes(_TINY_PNG)
    # Not an AI-takeover shot - must not show up here.
    (shot_dir / "erp-success.png").write_bytes(_TINY_PNG)

    make_user(db_session, role=SUPER_ADMIN, email="sa-shots@example.com")
    login(client, "sa-shots@example.com")

    resp = client.get(f"/api/v1/jobs/{job.id}/erp-debug-shots")
    assert resp.status_code == 200, resp.text
    shots = resp.json()["shots"]
    assert [s["name"] for s in shots] == ["ai-takeover-1.png", "ai-takeover-2.png"]
    assert all(s["data_url"].startswith("data:image/png;base64,") for s in shots)


def test_no_shots_yet_returns_an_empty_list_not_an_error(client, db_session):
    job, _tenant = _make_job(db_session)

    make_user(db_session, role=SUPER_ADMIN, email="sa-shots2@example.com")
    login(client, "sa-shots2@example.com")

    resp = client.get(f"/api/v1/jobs/{job.id}/erp-debug-shots")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"shots": []}


def test_tenant_admin_cannot_call_it(client, db_session):
    job, tenant = _make_job(db_session)

    make_user(db_session, role=TENANT_ADMIN, tenant=tenant, email="ta-shots@example.com")
    login(client, "ta-shots@example.com")

    resp = client.get(f"/api/v1/jobs/{job.id}/erp-debug-shots")
    assert resp.status_code == 403
