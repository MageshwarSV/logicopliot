"""When the AI service itself fails (OpenAI credits exhausted, rate limited, connection error,
5xx) mid-message, that message must NOT be recorded as "no customer identified" or "no
document matched" - those are permanent verdicts, and an outage has nothing to do with whether
the message matches a customer.

It is recorded as HELD UP instead (EmailSeen verdict "failed"), and the puller stops opening
mail for a while. It used to leave no record at all so "the very next poll tries again" - which
on production, with a one-minute poll and an account out of credits, was the same 39 mails
every minute for hours: a job made, every page OCR'd and written to the bill, the job removed.
See AI_DOWN_WAIT_MINUTES in app/core/email_puller.py."""

import io
from email.message import EmailMessage
from unittest.mock import MagicMock, patch

import fitz

from app.core.classifier_common import AIServiceUnavailable
from app.core.email_puller import _pull_one_mailbox, ai_wait_status, clear_ai_wait
from app.models.billable_page_event import BillablePageEvent
from app.models.custom_field import CustomField
from app.models.email_seen import EmailSeen
from app.models.job import Job
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant, make_user


def _pdf_bytes() -> bytes:
    doc = fitz.open()
    doc.new_page()
    data = doc.tobytes()
    doc.close()
    return data


def _fake_message(message_id: str) -> bytes:
    msg = EmailMessage()
    msg["Message-ID"] = message_id
    msg["From"] = "customer@example.com"
    msg["Subject"] = "Shipment docs"
    msg.set_content("Please find attached.")
    msg.add_attachment(_pdf_bytes(), maintype="application", subtype="pdf", filename="bl.pdf")
    return msg.as_bytes()


def _mock_conn_with_one_message(raw_bytes: bytes):
    conn = MagicMock()
    conn.search.return_value = ("OK", [b"1"])
    conn.fetch.return_value = ("OK", [(b"1 (BODY.PEEK[] {n}", raw_bytes)])
    return conn


def _setup(db_session, tenant):
    group = TemplateGroup(tenant_id=tenant.id, name="Sea Import", status="ready")
    db_session.add(group)
    db_session.flush()
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="BL", doc_type="BL", order_index=0)
    db_session.add(doc)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Test Consignee Co"))
    db_session.commit()
    return group, doc


def test_identify_customer_outage_leaves_the_message_completely_unclaimed(db_session):
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="outage-op@example.com")
    group, doc = _setup(db_session, tenant)
    # A second template so identify_customer is actually consulted (a single-candidate
    # mailbox bypasses it entirely - not the scenario being tested here).
    db_session.add(TemplateGroup(tenant_id=tenant.id, name="Air Import", status="ready"))
    db_session.commit()

    raw = _fake_message("<outage-1@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "consignee: Test Consignee Co", "tokens": []}), \
         patch("app.core.classifier_common.identify_customer",
               side_effect=AIServiceUnavailable("insufficient_quota")):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id,
                                   mail_email="outage-op@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True
    assert result["processed"][0]["deferred"] is True

    # No job was created and the IMAP flag was never touched. The mail is written down as held
    # up - never as "no customer" - with the reason, and the puller is now waiting.
    assert db_session.query(Job).count() == 0
    row = db_session.query(EmailSeen).filter(EmailSeen.message_id == "<outage-1@example.com>").one()
    assert row.verdict == "failed" and "insufficient_quota" in row.note
    assert ai_wait_status()["waiting"] is True
    conn.store.assert_not_called()


def test_classify_document_outage_rolls_back_the_tentative_job_and_unclaims(db_session):
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="outage-op2@example.com")
    group, doc = _setup(db_session, tenant)

    raw = _fake_message("<outage-2@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "consignee: Test Consignee Co", "tokens": []}), \
         patch("app.core.classifier_common.identify_customer",
               return_value={"keys": [group.id], "reason": "matched", "evidence": "Test Consignee Co"}), \
         patch("app.core.classifier_sea_import.assign_documents_detailed",
               side_effect=AIServiceUnavailable("rate limited")):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id,
                                   mail_email="outage-op2@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True
    assert result["processed"][0]["deferred"] is True
    # The job that was flushed mid-way through (before the AI call failed) must not survive -
    # a half-built job with no documents routed is worse than no job at all.
    assert db_session.query(Job).count() == 0
    assert db_session.query(EmailSeen).filter(
        EmailSeen.message_id == "<outage-2@example.com>").one().verdict == "failed"


def test_single_template_mailbox_outage_during_classification_discards_the_early_job(db_session):
    # A single-template mailbox creates its job immediately, before OCR even runs (see
    # test_email_puller_immediate_job.py) - unlike the shared-mailbox case above, that job
    # is already committed by the time classification can fail. Confirms it still gets
    # discarded rather than left behind as a half-built shell with no documents routed.
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="outage-op4@example.com")
    group, doc = _setup(db_session, tenant)

    raw = _fake_message("<outage-4@example.com>")
    conn = _mock_conn_with_one_message(raw)

    with patch("app.core.email_puller._connect", return_value=conn), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "some text", "tokens": []}), \
         patch("app.core.classifier_sea_import.assign_documents_detailed",
               side_effect=AIServiceUnavailable("rate limited")):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, forced_group_id=group.id,
                                   mail_email="outage-op4@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["ok"] is True
    assert result["processed"][0]["deferred"] is True
    # The job that briefly existed (so the operator could watch OCR happen) is gone, and
    # nothing was left behind for the retry to collide with.
    assert db_session.query(Job).count() == 0
    assert db_session.query(EmailSeen).filter(
        EmailSeen.message_id == "<outage-4@example.com>").one().verdict == "failed"
    # ...and its pages are not on the bill: they were written the moment they were OCR'd,
    # against a job that no longer exists and that nobody was ever given.
    assert db_session.query(BillablePageEvent).count() == 0


def test_a_held_message_is_not_reopened_while_waiting_and_succeeds_once_the_wait_ends(db_session):
    # While the puller is holding off, the same mail is NOT opened again - no OCR, no job, no
    # bill. Once the wait ends, a completely ordinary poll (no reexamine) reads it.
    tenant = make_tenant(db_session)
    owner = make_user(db_session, role="operator", tenant=tenant, email="outage-op3@example.com")
    group, doc = _setup(db_session, tenant)
    # A second template so identify_customer is actually consulted rather than bypassed by
    # the single-connected-template shortcut (see the first test above for the same note).
    db_session.add(TemplateGroup(tenant_id=tenant.id, name="Air Import 3", status="ready"))
    db_session.commit()

    raw = _fake_message("<outage-3@example.com>")

    conn1 = _mock_conn_with_one_message(raw)
    with patch("app.core.email_puller._connect", return_value=conn1), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "consignee: Test Consignee Co", "tokens": []}), \
         patch("app.core.classifier_common.identify_customer",
               side_effect=AIServiceUnavailable("insufficient_quota")):
        first = _pull_one_mailbox(db_session, tenant_id=tenant.id,
                                  mail_email="outage-op3@zoho.com", mail_app_password="x",
                                  mail_host="imap.zoho.com")
    assert first["processed"][0]["deferred"] is True
    assert db_session.query(Job).count() == 0

    # Still waiting: the next poll, a minute later, must not touch the mail at all.
    ocr = MagicMock(return_value={"text": "consignee: Test Consignee Co", "tokens": []})
    with patch("app.core.email_puller._connect", return_value=_mock_conn_with_one_message(raw)), \
         patch("app.core.docai.ocr_page_image", ocr), \
         patch("app.core.classifier_common.identify_customer") as identify:
        again = _pull_one_mailbox(db_session, tenant_id=tenant.id,
                                  mail_email="outage-op3@zoho.com", mail_app_password="x",
                                  mail_host="imap.zoho.com")
    assert again["processed"][0]["deferred"] is True
    ocr.assert_not_called()
    identify.assert_not_called()
    assert db_session.query(Job).count() == 0
    assert db_session.query(BillablePageEvent).count() == 0

    # The wait ends (or a Super Admin ended it) - a completely ordinary poll, reexamine=False,
    # same message still sitting UNSEEN in the mailbox (nothing ever marked it read).
    clear_ai_wait()
    conn2 = _mock_conn_with_one_message(raw)
    with patch("app.core.email_puller._connect", return_value=conn2), \
         patch("app.core.docai.ocr_page_image", return_value={"text": "consignee: Test Consignee Co", "tokens": []}), \
         patch("app.core.classifier_common.identify_customer",
               return_value={"keys": [group.id], "reason": "matched", "evidence": "Test Consignee Co"}), \
         patch("app.core.classifier_sea_import.assign_documents_detailed",
               return_value=[[{"key": doc.id, "pages": [1], "evidence": "bill of lading"}]]):
        second = _pull_one_mailbox(db_session, tenant_id=tenant.id,
                                   mail_email="outage-op3@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert second["ok"] is True
    assert not second["processed"][0].get("deferred")
    assert db_session.query(Job).count() == 1
    db_session.expire_all()
    assert db_session.query(EmailSeen).filter(
        EmailSeen.message_id == "<outage-3@example.com>").one().verdict == "matched"


def test_a_mail_arriving_while_waiting_is_recorded_without_being_opened(db_session):
    tenant = make_tenant(db_session)
    make_user(db_session, role="operator", tenant=tenant, email="outage-op5@example.com")
    group, doc = _setup(db_session, tenant)
    from app.core.email_puller import _note_ai_down

    _note_ai_down("You have no credits remaining")
    ocr = MagicMock()
    with patch("app.core.email_puller._connect",
               return_value=_mock_conn_with_one_message(_fake_message("<outage-5@example.com>"))), \
         patch("app.core.docai.ocr_page_image", ocr):
        result = _pull_one_mailbox(db_session, tenant_id=tenant.id, forced_group_id=group.id,
                                   mail_email="outage-op5@zoho.com", mail_app_password="x",
                                   mail_host="imap.zoho.com")

    assert result["processed"][0]["deferred"] is True
    ocr.assert_not_called()
    assert db_session.query(Job).count() == 0
    row = db_session.query(EmailSeen).filter(EmailSeen.message_id == "<outage-5@example.com>").one()
    assert row.verdict == "failed" and "no credits" in row.note and row.group_id == group.id


def test_waiting_never_overwrites_a_mail_that_already_has_a_real_outcome(db_session):
    from app.core.email_puller import _note_ai_down, _record_failed

    db_session.add(EmailSeen(message_id="<done@example.com>", verdict="matched", note="job made"))
    db_session.commit()
    _note_ai_down("down")
    _record_failed(db_session, "<done@example.com>", "a@b.c", "subject", "Waiting")
    db_session.expire_all()
    assert db_session.query(EmailSeen).filter(
        EmailSeen.message_id == "<done@example.com>").one().verdict == "matched"


def test_super_admin_sees_the_held_mails_and_can_rerun_them(client, db_session):
    from app.core.email_puller import _note_ai_down, _record_failed
    from tests.conftest import login

    make_user(db_session, role="super_admin", email="sa-held@example.com")
    _note_ai_down("You have no credits remaining")
    _record_failed(db_session, "<held-1@example.com>", "ops@customer.com", "FW: HBL 123",
                   "The AI service is unavailable: no credits")
    login(client, "sa-held@example.com")

    body = client.get("/api/v1/email/failed").json()
    assert body["waiting"] is True and body["total"] == 1
    assert body["items"][0]["subject"] == "FW: HBL 123"
    assert "no credits" in body["items"][0]["reason"]

    with patch("app.core.email_puller.pull_inbox") as pull:
        r = client.post("/api/v1/email/failed/rerun")
    assert r.status_code == 200 and r.json() == {"queued": 1}
    assert ai_wait_status()["waiting"] is False
    for _ in range(50):                      # the pull runs on its own thread
        if pull.called:
            break
        import time
        time.sleep(0.02)
    assert pull.called


def test_pages_of_attempts_that_never_became_a_job_can_be_taken_off_the_bill(client, db_session):
    from datetime import datetime, timedelta, timezone

    from tests.conftest import login

    tenant = make_tenant(db_session)
    make_user(db_session, role="super_admin", email="sa-bill@example.com")
    group, _doc = _setup(db_session, tenant)
    live = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-LIVE", status="extracted")
    db_session.add(live)
    db_session.flush()
    for job_id, ref, pages in ((live.id, "JOB-LIVE", 9), ("gone-1", "JOB-GONE1", 7), ("gone-2", "JOB-GONE2", 6)):
        db_session.add(BillablePageEvent(tenant_id=tenant.id, job_id=job_id, job_reference=ref,
                                         shipment_type="Sea Import", page_count=pages))
    db_session.commit()
    login(client, "sa-bill@example.com")
    now = datetime.now(timezone.utc)
    window = {"start": (now - timedelta(hours=1)).isoformat(), "end": (now + timedelta(hours=1)).isoformat()}

    preview = client.post("/api/v1/email/failed/uncount-pages", json=window).json()
    assert preview == {"jobs": 2, "pages": 13, "records": 2, "applied": False}
    assert db_session.query(BillablePageEvent).count() == 3            # a look changes nothing

    done = client.post("/api/v1/email/failed/uncount-pages", json={**window, "apply": True}).json()
    assert done["applied"] is True and done["pages"] == 13
    db_session.expire_all()
    left = db_session.query(BillablePageEvent).all()
    assert [(e.job_reference, e.page_count) for e in left] == [("JOB-LIVE", 9)]   # a real job's pages stay

    outside = {"start": (now - timedelta(days=3)).isoformat(), "end": (now - timedelta(days=2)).isoformat(), "apply": True}
    assert client.post("/api/v1/email/failed/uncount-pages", json=outside).json()["records"] == 0


def test_a_held_mail_from_an_earlier_day_is_fetched_by_its_own_id(db_session):
    """The routine search is "unread, arrived today". A mail that failed yesterday evening is
    not found by it after midnight - it has to be looked up by its Message-ID, or it would sit
    on the held list for ever and Rerun all would do nothing for it."""
    from app.core.email_puller import _harvest_mailbox

    conn = MagicMock()

    def search(_charset, *criteria):
        if criteria[0] == "HEADER":
            return ("OK", [b"7"]) if criteria[2] == '"<yesterday@example.com>"' else ("OK", [b""])
        return ("OK", [b"3"])                       # today's unread mail

    conn.search.side_effect = search
    conn.fetch.side_effect = lambda uid, _what: ("OK", [(b"x", b"raw-" + uid)])
    with patch("app.core.email_puller._connect", return_value=conn):
        got = _harvest_mailbox("box@example.com", "x", "imap.example.com", 30, False,
                               held_message_ids=["<yesterday@example.com>", "<other-box@example.com>"])

    assert got["ok"] is True
    assert [m["uid"] for m in got["messages"]] == [b"3", b"7"]
    # and with nothing held, exactly the search it always made
    conn.search.reset_mock()
    with patch("app.core.email_puller._connect", return_value=conn):
        _harvest_mailbox("box@example.com", "x", "imap.example.com", 30, False)
    assert conn.search.call_count == 1


def test_mails_can_be_put_on_the_held_list_without_touching_ones_already_dealt_with(client, db_session):
    from tests.conftest import login

    make_user(db_session, role="super_admin", email="sa-hold@example.com")
    db_session.add(EmailSeen(message_id="<has-a-job@example.com>", verdict="matched", note="job made"))
    db_session.commit()
    login(client, "sa-hold@example.com")

    r = client.post("/api/v1/email/failed/hold", json={"items": [
        {"message_id": "<stuck-1@example.com>", "sender": "malar@customer.com", "subject": "OCEAN PRE ALERT 1"},
        {"message_id": "<stuck-2@example.com>", "sender": "malar@customer.com", "subject": "OCEAN PRE ALERT 2"},
        {"message_id": "<has-a-job@example.com>", "sender": "x@y.z", "subject": "already a job"},
    ]})
    assert r.status_code == 200 and r.json() == {"held": 2, "left_alone": 1}
    db_session.expire_all()
    verdicts = {e.message_id: e.verdict for e in db_session.query(EmailSeen).all()}
    assert verdicts == {"<stuck-1@example.com>": "failed", "<stuck-2@example.com>": "failed",
                        "<has-a-job@example.com>": "matched"}
    listed = client.get("/api/v1/email/failed").json()
    assert listed["total"] == 2 and listed["items"][0]["subject"] == "OCEAN PRE ALERT 1"
    # doing it twice changes nothing
    again = client.post("/api/v1/email/failed/hold", json={"items": [{"message_id": "<stuck-1@example.com>"}]})
    assert again.json() == {"held": 1, "left_alone": 0}
    assert client.get("/api/v1/email/failed").json()["total"] == 2
