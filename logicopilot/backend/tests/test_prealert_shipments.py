"""One pre-alert email covering several shipments -> one mail (and so one job) per shipment, and
an Air Waybill built from the pre-alert when none is attached. See app/core/prealert_shipments.py.

The example is the real one the feature was specified against:
    FW: Pre-Alert: Juniper Networks Solution India Pvt //45T0523243 //4720797358// 4720797552
"""
import email
from email.message import EmailMessage

import fitz

from app.core import prealert_shipments as ps

SUBJECT = "FW: Pre-Alert: Juniper Networks Solution India Pvt //45T0523243 //4720797358// 4720797552"
SHIPMENTS = [
    {"hawb": "45T0523243", "file_no": "22H0523041", "mawb": "176-23418102", "origin": "HAN",
     "destination": "BLR", "pieces": "9", "gross_weight": "97", "chargeable_weight": "146",
     "shipper": "Fuhong Precision Component", "consignee": "Juniper Networks Solution India Pvt",
     "eta": "14-11-2026"},
    {"hawb": "4720797358", "file_no": "22H0523049", "mawb": "", "origin": "", "destination": "",
     "pieces": "4", "gross_weight": "23", "chargeable_weight": "", "shipper": "Celestica",
     "consignee": "", "eta": "14-11-2026"},
    {"hawb": "4720797552", "file_no": "22H0523050", "mawb": "", "origin": "", "destination": "",
     "pieces": "6", "gross_weight": "54.5", "chargeable_weight": "", "shipper": "Celestica",
     "consignee": "", "eta": "14-11-2026"},
]


def _pdf(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((50, 80), text, fontsize=11)
    out = doc.tobytes()
    doc.close()
    return out


FILES = [("22H0523041.pdf", _pdf("Commercial Invoice")), ("22H0523049.pdf", _pdf("Commercial Invoice")),
         ("22H0523050.pdf", _pdf("Commercial Invoice"))]


def _mail(subject=SUBJECT, files=FILES) -> EmailMessage:
    m = EmailMessage()
    m["From"] = "Forwarder <ops@forwarder.example>"
    m["To"] = "airimport@cargora.example"
    m["Subject"] = subject
    m["Message-ID"] = "<abc123@forwarder.example>"
    m.set_content("FILE_NO ETA SHIPPER HAWB PCS G_WGT ...")
    for name, blob in files:
        m.add_attachment(blob, maintype="application", subtype="pdf", filename=name)
    return m


# ------------------------------------------------------------------ the subject

def test_every_hawb_after_the_slashes_is_read_in_order():
    assert ps.hawbs_from_subject(SUBJECT) == ["45T0523243", "4720797358", "4720797552"]


def test_one_hawb_is_a_single_shipment():
    assert ps.hawbs_from_subject("FW: Pre-Alert: Juniper //45T0523243") == ["45T0523243"]


def test_a_repeated_hawb_counts_once_and_short_tokens_are_not_hawbs():
    assert ps.hawbs_from_subject("Pre-Alert //45T0523243 // 45t0523243 //FW //12") == ["45T0523243"]


def test_a_subject_with_no_slashes_has_none():
    assert ps.hawbs_from_subject("Pre-Alert: Juniper Networks 45T0523243") == []


# The other way it is written - the Expeditors mail pulled as ONE job (JOB-90C47F, 17 Nov 2026).
EXPEDITORS_SUBJECT = "Fwd:\xa0Pre-Alert | HAWB - 45T0524120, 45T0523984 | Juniper Networks Solution India Pvt"


def test_hawbs_listed_after_the_word_hawb_are_read():
    assert ps.hawbs_from_subject(EXPEDITORS_SUBJECT) == ["45T0524120", "45T0523984"]
    assert ps.hawbs_from_subject("Pre-Alert HAWB: 45T0524120 / 45T0523984 & 4720797358") == [
        "45T0524120", "45T0523984", "4720797358"]
    assert ps.hawbs_from_subject("Pre-Alert HAWBs 45T0524120 and 45T0523984") == ["45T0524120", "45T0523984"]
    assert ps.hawbs_from_subject("Pre-Alert HAWB No. 45T0524120") == ["45T0524120"]


def test_the_hawb_list_stops_at_a_name_and_never_reads_mawb():
    # "2026" after a name is not a HAWB; numbers after MAWB are not HAWBs.
    assert ps.hawbs_from_subject("Pre-Alert HAWB 45T0524120 Juniper Networks 2026") == ["45T0524120"]
    assert ps.hawbs_from_subject("Pre-Alert | MAWB - 312-93440325, 198-00112210 | HAWB 45T0524120") == ["45T0524120"]
    assert ps.hawbs_from_subject("Pre-Alert | MAWB - 312-93440325, 198-00112210") == []


def test_both_ways_together_count_each_hawb_once():
    found = ps.hawbs_from_subject("Pre-Alert HAWB - 45T0524120, 45T0523984 //45t0524120")
    assert [h.upper() for h in found] == ["45T0524120", "45T0523984"]


def test_the_expeditors_mail_splits_into_one_mail_per_shipment(monkeypatch):
    shipments = [{**SHIPMENTS[0], "hawb": "45T0524120", "file_no": "22H0523553"},
                 {**SHIPMENTS[1], "hawb": "45T0523984", "file_no": "22H0523560"}]
    files = [("22H0523553.pdf", _pdf("Commercial Invoice")), ("22H0523560.pdf", _pdf("Commercial Invoice"))]
    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [dict(x) for x in shipments])
    subs = ps.plan_split(_mail(EXPEDITORS_SUBJECT, files), EXPEDITORS_SUBJECT, "body", list(files))
    assert len(subs) == 2
    for raw, shipment, fname in zip(subs, shipments, ["22H0523553.pdf", "22H0523560.pdf"]):
        sub = email.message_from_bytes(raw)
        # The list is taken out; each mail names only its own HAWB, the usual way.
        assert sub["Subject"] == f"Fwd: Pre-Alert | Juniper Networks Solution India Pvt //{shipment['hawb']}"
        assert ps.hawbs_from_subject(sub["Subject"]) == [shipment["hawb"]]
        assert [p.get_filename() for p in sub.walk() if p.get_filename()] == [fname]


# ------------------------------------------------------------------ HAWBs in the BODY
# The Expeditors table, as the mail's HTML has it - with a subject that names no HAWB at all.
BODY_HTML = """<p>Dear Team,</p><p>Please find the Pre-alert documents for the subject Shipments.</p>
<table><tr><th>FILE_NO</th><th>ETA</th><th>SHIPPER_CLIENT_NAME</th><th>CONSIGNEE_CLIENT_NAME</th>
<th>ORIGIN</th><th>DESTINATION</th><th>MASTERBILL_NO</th><th>HOUSEBILL_NO</th><th>PCS</th></tr>
<tr><td>22H0523553</td><td>19-Nov-26</td><td>Juniper Networks International B.V.</td><td>Juniper Networks Solution India Pvt</td>
<td>HAN</td><td>BLR</td><td>312-93440325</td><td>45T0524120</td><td>2</td></tr>
<tr><td>22H0523560</td><td>20-Nov-26</td><td>VIETNAM ACCTON TECHNOLOGY CO LTD</td><td>Juniper Networks Solution India Pvt</td>
<td>HAN</td><td>BLR</td><td>198-00112210</td><td>45T0523984</td><td>6</td></tr></table>"""
BODY_SHIPMENTS = [{**SHIPMENTS[0], "hawb": "45T0524120", "file_no": "22H0523553", "mawb": "312-93440325"},
                  {**SHIPMENTS[1], "hawb": "45T0523984", "file_no": "22H0523560", "mawb": "198-00112210"}]
BODY_FILES = [("22H0523553.pdf", _pdf("Commercial Invoice")), ("22H0523560.pdf", _pdf("Commercial Invoice"))]


def _html_mail(subject, html, files):
    m = EmailMessage()
    m["From"] = "Forwarder <ops@forwarder.example>"
    m["To"] = "airimport@cargora.example"
    m["Subject"] = subject
    m["Message-ID"] = "<body123@forwarder.example>"
    m.set_content("see table")
    m.add_alternative(html, subtype="html")
    for name, blob in files:
        m.add_attachment(blob, maintype="application", subtype="pdf", filename=name)
    return m


def test_hawbs_are_read_from_a_housebill_column_never_the_masterbill_one():
    assert ps.hawbs_from_body("", BODY_HTML) == ["45T0524120", "45T0523984"]


def test_hawbs_are_read_from_hawb_lines_in_the_text():
    body = "Shipment 1\nHAWB: 45T0524120\nMAWB: 312-93440325\n\nShipment 2\nHouse AWB No - 45T0523984"
    assert ps.hawbs_from_body(body) == ["45T0524120", "45T0523984"]
    assert ps.hawbs_from_body("MAWB: 312-93440325 and nothing else") == []


def test_a_mail_naming_several_hawbs_only_in_its_body_is_worth_checking():
    msg = _html_mail("Pre-Alert | Juniper Networks Solution India Pvt", BODY_HTML, BODY_FILES)
    assert ps.may_cover_several(msg, msg["Subject"], "see table") is True
    one = BODY_HTML.split("<tr><td>22H0523560")[0] + "</table>"
    assert ps.may_cover_several(_html_mail("Pre-Alert", one, []), "Pre-Alert", "") is False


def test_a_mail_naming_its_shipments_only_in_the_body_is_split(monkeypatch):
    monkeypatch.setattr(ps, "read_shipments", lambda subject, body, hawbs, **k: [
        dict(x) for x in BODY_SHIPMENTS if x["hawb"] in hawbs])
    subject = "Pre-Alert | Juniper Networks Solution India Pvt"
    msg = _html_mail(subject, BODY_HTML, BODY_FILES)
    subs = ps.plan_split(msg, subject, "see table", list(BODY_FILES))
    assert len(subs) == 2
    for raw, shipment, fname in zip(subs, BODY_SHIPMENTS, ["22H0523553.pdf", "22H0523560.pdf"]):
        sub = email.message_from_bytes(raw)
        assert sub["Subject"] == f"{subject} //{shipment['hawb']}"
        assert [p.get_filename() for p in sub.walk() if p.get_filename()] == [fname]


def test_a_shipment_named_only_in_a_quoted_older_table_gets_no_job(monkeypatch):
    # Subject names one HAWB; the body also quotes an older mail's table with another. That
    # other shipment has no attachment here, so it is not this mail's - no split at all.
    monkeypatch.setattr(ps, "read_shipments", lambda subject, body, hawbs, **k: [
        dict(x) for x in BODY_SHIPMENTS if x["hawb"] in hawbs])
    subject = "Pre-Alert | HAWB - 45T0524120 | Juniper"
    files = [BODY_FILES[0]]
    assert ps.plan_split(_html_mail(subject, BODY_HTML, files), subject, "x", list(files)) is None


# ------------------------------------------------------------------ attachments

def test_attachments_are_matched_by_the_file_number_in_their_name():
    placed, loose = ps.assign_attachments(SHIPMENTS, FILES)
    assert placed == {"45T0523243": [0], "4720797358": [1], "4720797552": [2]}
    assert loose == []


def test_an_attachment_is_matched_by_the_hawb_printed_inside_it():
    files = [("scan_001.pdf", _pdf("House Air Waybill 4720797358  Celestica"))]
    placed, loose = ps.assign_attachments(SHIPMENTS, files)
    assert placed["4720797358"] == [0] and loose == []


def test_an_attachment_nobody_can_place_is_left_out_not_guessed(monkeypatch):
    monkeypatch.setattr(ps, "_assign_with_model", lambda *a, **k: {})
    placed, loose = ps.assign_attachments(SHIPMENTS, [("terms.pdf", _pdf("General terms and conditions"))])
    assert all(v == [] for v in placed.values())
    assert loose == [0]


# ------------------------------------------------------------------ the generated waybill

def test_the_generated_waybill_prints_what_the_pre_alert_gave():
    with fitz.open(stream=ps.build_awb_pdf(SHIPMENTS[0]), filetype="pdf") as doc:
        text = " ".join(doc[0].get_text().split())
    assert doc is not None and "AIR WAYBILL (PRE-ALERT)" in text
    for value in ("45T0523243", "176-23418102", "HAN", "BLR", "9", "97 KGS", "Fuhong Precision Component"):
        assert value in text
    assert ps.awb_filename(SHIPMENTS[0]) == "AWB_45T0523243.pdf"


def test_what_the_pre_alert_did_not_say_is_left_off_the_waybill():
    with fitz.open(stream=ps.build_awb_pdf(SHIPMENTS[1]), filetype="pdf") as doc:
        text = doc[0].get_text()
    assert "MAWB Number" not in text and "Airport of Departure" not in text
    assert "4720797358" in text


# ------------------------------------------------------------------ the split

def test_three_shipments_become_three_mails_each_with_its_own_papers(monkeypatch):
    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [dict(s) for s in SHIPMENTS])
    msg = _mail()
    subs = ps.plan_split(msg, SUBJECT, "body", [(n, b) for n, b in FILES])
    assert len(subs) == 3
    for raw, shipment, fname in zip(subs, SHIPMENTS, ["22H0523041.pdf", "22H0523049.pdf", "22H0523050.pdf"]):
        sub = email.message_from_bytes(raw)
        assert sub["Subject"] == f"FW: Pre-Alert: Juniper Networks Solution India Pvt //{shipment['hawb']}"
        assert ps.hawbs_from_subject(sub["Subject"]) == [shipment["hawb"]]
        assert shipment["hawb"] in sub["Message-ID"]
        assert sub["From"] == "Forwarder <ops@forwarder.example>"
        assert ps.shipment_from_message(sub) == shipment          # survives header folding
        assert [p.get_filename() for p in sub.walk() if p.get_filename()] == [fname]
    assert len({email.message_from_bytes(r)["Message-ID"] for r in subs}) == 3


def test_a_split_mail_is_never_split_again(monkeypatch):
    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [dict(s) for s in SHIPMENTS])
    subs = ps.plan_split(_mail(), SUBJECT, "body", list(FILES))
    child = email.message_from_bytes(subs[0])
    assert ps.plan_split(child, child["Subject"], "body", []) is None


def test_a_single_shipment_mail_with_one_attachment_costs_no_model_call(monkeypatch):
    called = []
    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: called.append(1) or [])
    subject = "FW: Pre-Alert: Juniper //45T0523243"
    one = [FILES[0]]
    assert ps.plan_split(_mail(subject, one), subject, "body", list(one)) is None
    assert called == []          # one attachment can never be two shipments - the model is not asked


def test_with_several_attachments_the_model_decides_and_one_shipment_is_left_alone(monkeypatch):
    calls = []

    def fake(subject, body, hawbs, **k):
        calls.append(k.get("discover"))
        return [dict(SHIPMENTS[0])]
    monkeypatch.setattr(ps, "read_shipments", fake)
    subject = "FW: Pre-Alert: Juniper //45T0523243"
    found = {}
    assert ps.plan_split(_mail(subject), subject, "body", list(FILES), found=found) is None
    assert calls == [True] and found["single"]["hawb"] == "45T0523243"   # handed on, not asked again


def test_the_model_splits_a_subject_format_no_rule_knows(monkeypatch):
    # "Shipments 45T0523243 & 4720797358 ..." - no "//", no "HAWB" word, no table.
    subject = "Pre-alert docs for shipments 45T0523243 & 4720797358 (Juniper)"
    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [dict(SHIPMENTS[0]), dict(SHIPMENTS[1])])
    files = FILES[:2]
    subs = ps.plan_split(_mail(subject, files), subject, "please find attached", list(files))
    assert [email.message_from_bytes(r)["Subject"].rsplit("//", 1)[1] for r in subs] == ["45T0523243", "4720797358"]


def test_a_hawb_the_model_invents_is_ignored(monkeypatch):
    subject = "Pre-alert docs for shipment 45T0523243"
    invented = {**SHIPMENTS[1], "hawb": "9999999999"}           # printed nowhere in the mail
    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [dict(SHIPMENTS[0]), invented])
    assert ps.plan_split(_mail(subject, FILES[:2]), subject, "body", list(FILES[:2])) is None


def test_several_numbers_in_the_subject_but_one_readable_shipment_is_not_split(monkeypatch):
    """A subject that merely LOOKS like three shipments must never become three empty jobs."""
    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [dict(SHIPMENTS[0])])
    assert ps.plan_split(_mail(), SUBJECT, "body", list(FILES)) is None


# ------------------------------------------------------------------ filing it on the job

def _job_with_slots(db_session):
    from app.models.job import Job, JobDocument
    from app.models.template_document import TemplateDocument
    from app.models.template_group import TemplateGroup
    from tests.conftest import make_tenant

    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="approved", prealert_shipments=True)
    db_session.add(group)
    db_session.flush()
    awb = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Air Waybill", doc_type="BL", order_index=1)
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice", order_index=2)
    db_session.add_all([awb, inv])
    db_session.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-PREALERT", status="draft")
    db_session.add(job)
    db_session.flush()
    slots = {}
    for tdoc in (awb, inv):
        jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id, page_count=0)
        db_session.add(jd)
        slots[tdoc.id] = jd
    db_session.commit()
    return group, awb, job, slots


def test_the_generated_waybill_is_filed_even_though_the_puller_s_template_is_detached(
        db_session, tmp_path, monkeypatch):
    """The first real mail (JOB-703FCC) was split correctly and then got NO waybill: the puller
    holds its template on another session, and reading template.documents off it raised
    DetachedInstanceError. The slot is found by a query on the group id instead."""
    import app.api.v1.jobs as jobs_mod
    from app.core.email_puller import _file_prealert_awb

    group, awb, job, slots = _job_with_slots(db_session)
    monkeypatch.setattr(jobs_mod, "_job_doc_dir", lambda jd_id: tmp_path / jd_id)
    group_id = group.id
    db_session.expunge(group)                       # exactly the puller's situation
    filled, named = [], []

    assert _file_prealert_awb(db_session, group_id, job, slots, lambda: dict(SHIPMENTS[0]), filled, named) is True

    slot = slots[awb.id]
    assert slot.original_name == "AWB_45T0523243.pdf" and slot.page_count == 1
    assert (tmp_path / slot.id / "AWB_45T0523243.pdf").is_file()
    assert filled == ["Air Waybill"]


def test_a_mail_that_brought_its_own_waybill_is_left_alone_and_costs_no_model_call(
        db_session, tmp_path, monkeypatch):
    import app.api.v1.jobs as jobs_mod
    from app.core.email_puller import _file_prealert_awb

    group, awb, job, slots = _job_with_slots(db_session)
    monkeypatch.setattr(jobs_mod, "_job_doc_dir", lambda jd_id: tmp_path / jd_id)
    slots[awb.id].file_path = "x/real_awb.pdf"
    asked = []

    assert _file_prealert_awb(db_session, group.id, job, slots,
                              lambda: asked.append(1) or dict(SHIPMENTS[0]), [], []) is False
    assert asked == [] and slots[awb.id].file_path == "x/real_awb.pdf"


def test_no_shipment_data_means_no_waybill(db_session, tmp_path, monkeypatch):
    import app.api.v1.jobs as jobs_mod
    from app.core.email_puller import _file_prealert_awb

    group, awb, job, slots = _job_with_slots(db_session)
    monkeypatch.setattr(jobs_mod, "_job_doc_dir", lambda jd_id: tmp_path / jd_id)
    assert _file_prealert_awb(db_session, group.id, job, slots, lambda: None, [], []) is False
    assert slots[awb.id].file_path is None


def test_the_generated_waybill_carries_no_date_at_all():
    """The ETA is the pre-alert's only date; printed, it was read as the MAWB Date."""
    with fitz.open(stream=ps.build_awb_pdf(SHIPMENTS[0]), filetype="pdf") as doc:
        text = doc[0].get_text()
    assert "ETA" not in text and "14-11-2026" not in text


# ------------------------------------------------------------------ one task per shipment

def _base_task(db_session, prealert=True):
    from types import SimpleNamespace

    from tests.conftest import make_tenant

    tenant = make_tenant(db_session)
    group = SimpleNamespace(id="g1", name="Air import", tenant_id=tenant.id, prealert_shipments=prealert)
    return {"src": {"email": "a@b", "password": "x", "host": "h"}, "ctx": {"single_group": group},
            "uid": b"1", "raw": _mail().as_bytes(), "extraction_engine": "e",
            "vision_engine_model": "m", "custom_filter_texts": []}


def test_a_multi_shipment_mail_becomes_one_task_per_shipment(db_session, monkeypatch):
    from app.core import email_puller as ep
    from app.models.email_seen import EmailSeen

    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [dict(s) for s in SHIPMENTS])
    monkeypatch.setattr(ep, "_start_claim_heartbeat", lambda mid: None)
    tasks, parent = ep._split_into_shipment_tasks(db_session, _base_task(db_session), False)

    assert len(tasks) == 3 and parent["shipments"] == 3
    assert all(t["split_checked"] and t["parent_mid"] == parent["mid"] for t in tasks)
    assert len({t["mid"] for t in tasks}) == 3
    subjects = [email.message_from_bytes(t["raw"])["Subject"] for t in tasks]
    assert [ps.hawbs_from_subject(x) for x in subjects] == [["45T0523243"], ["4720797358"], ["4720797552"]]
    # the mail itself is claimed, so the next poll does not split it again meanwhile
    assert db_session.query(EmailSeen).filter(EmailSeen.message_id == parent["mid"]).one().verdict == "working"


def test_a_single_shipment_mail_is_one_ordinary_task_and_costs_no_model_call(db_session, monkeypatch):
    from app.core import email_puller as ep

    called = []
    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: called.append(1) or [])
    base = _base_task(db_session)
    base["raw"] = _mail("FW: Pre-Alert: Juniper //45T0523243", [FILES[0]]).as_bytes()
    tasks, parent = ep._split_into_shipment_tasks(db_session, base, False)
    assert parent is None and len(tasks) == 1 and tasks[0]["split_checked"] is True
    assert tasks[0]["raw"] == base["raw"] and called == []


def test_the_shipment_the_model_read_is_handed_to_the_worker(db_session, monkeypatch):
    from app.core import email_puller as ep

    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [dict(SHIPMENTS[0])])
    base = _base_task(db_session)
    base["raw"] = _mail("FW: Pre-Alert: Juniper //45T0523243").as_bytes()
    tasks, parent = ep._split_into_shipment_tasks(db_session, base, False)
    assert parent is None and len(tasks) == 1
    assert tasks[0]["prealert_rec"]["hawb"] == "45T0523243"


def test_a_template_without_the_setting_is_never_split(db_session, monkeypatch):
    from app.core import email_puller as ep

    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [dict(s) for s in SHIPMENTS])
    base = _base_task(db_session, prealert=False)
    tasks, parent = ep._split_into_shipment_tasks(db_session, base, False)
    assert parent is None and tasks == [base]


def test_a_mail_that_cannot_be_split_releases_its_claim(db_session, monkeypatch):
    from app.core import email_puller as ep
    from app.models.email_seen import EmailSeen

    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [])       # body not readable
    tasks, parent = ep._split_into_shipment_tasks(db_session, _base_task(db_session), False)
    assert parent is None and len(tasks) == 1 and tasks[0]["split_checked"] is True
    assert db_session.query(EmailSeen).count() == 0                     # the worker claims it afresh


def _claimed_parent(db_session, monkeypatch):
    from app.core import email_puller as ep

    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [dict(s) for s in SHIPMENTS])
    monkeypatch.setattr(ep, "_start_claim_heartbeat", lambda mid: None)
    tasks, parent = ep._split_into_shipment_tasks(db_session, _base_task(db_session), False)
    return ep, tasks, parent


def test_all_shipments_done_records_the_mail_as_split(db_session, monkeypatch):
    from app.models.email_seen import EmailSeen

    ep, tasks, parent = _claimed_parent(db_session, monkeypatch)
    parent["outcomes"] = [(t["mid"], {"job_id": f"job{i}"}) for i, t in enumerate(tasks)]
    out = ep._settle_split_parent(db_session, parent)
    assert "3 job(s) created" in out["reason"] and not out.get("deferred")
    assert db_session.query(EmailSeen).filter(EmailSeen.message_id == parent["mid"]).one().verdict == "split"


def test_one_shipment_deferred_releases_the_mail_for_the_next_poll(db_session, monkeypatch):
    from app.models.email_seen import EmailSeen

    ep, tasks, parent = _claimed_parent(db_session, monkeypatch)
    parent["outcomes"] = [(tasks[0]["mid"], {"job_id": "j1"}), (tasks[1]["mid"], {"deferred": True}),
                          (tasks[2]["mid"], {"job_id": "j3"})]
    out = ep._settle_split_parent(db_session, parent)
    assert out["deferred"] is True
    assert db_session.query(EmailSeen).filter(EmailSeen.message_id == parent["mid"]).count() == 0


def test_a_shipment_already_made_on_an_earlier_poll_counts_as_done(db_session, monkeypatch):
    from app.models.email_seen import EmailSeen

    ep, tasks, parent = _claimed_parent(db_session, monkeypatch)
    parent["outcomes"] = [(tasks[0]["mid"], {"skipped_by_log": True, "reason": "already examined (matched)"}),
                          (tasks[1]["mid"], {"job_id": "j2"}), (tasks[2]["mid"], {"job_id": "j3"})]
    out = ep._settle_split_parent(db_session, parent)
    assert not out.get("deferred")
    assert db_session.query(EmailSeen).filter(EmailSeen.message_id == parent["mid"]).one().verdict == "split"


# ------------------------------------------------------------------ no dates on a generated waybill

def _dated_job(db_session, tmp_path, generated: bool):
    from app.models.custom_field import CustomField
    from app.models.job import JobFieldValue

    group, awb, job, slots = _job_with_slots(db_session)
    inv_id = next(k for k in slots if k != awb.id)
    path = tmp_path / ("AWB_45T0523243.pdf" if generated else "carrier_awb.pdf")
    path.write_bytes(ps.build_awb_pdf(SHIPMENTS[0]) if generated else _pdf("AIR WAYBILL 176-23418102 Executed on 11-Nov-2026"))
    slots[awb.id].file_path = str(path)
    fields = {}
    for label, sources, value in (("MAWB Date", [awb.id], "22-HAN-2023"), ("HBL Date", [awb.id], "11-Nov-2026"),
                                  ("Invoice Due Date", [inv_id], "09-Nov-2026"),
                                  ("Any Date", [awb.id, inv_id], "09-Nov-2026")):
        cf = CustomField(tenant_id=job.tenant_id, group_id=group.id, label_name=label, kind="ai",
                         date_picker=True, source_document_ids=sources)
        db_session.add(cf)
        db_session.flush()
        fields[label] = JobFieldValue(tenant_id=job.tenant_id, job_id=job.id, custom_field_id=cf.id,
                                      label_name=label, extracted_value=value)
    plain = CustomField(tenant_id=job.tenant_id, group_id=group.id, label_name="MAWB Number", kind="ai",
                        source_document_ids=[awb.id])
    db_session.add(plain)
    db_session.flush()
    fields["MAWB Number"] = JobFieldValue(tenant_id=job.tenant_id, job_id=job.id, custom_field_id=plain.id,
                                          label_name="MAWB Number", extracted_value="176-23418102")
    db_session.add_all(fields.values())
    db_session.commit()
    return job, fields


def test_a_generated_waybill_is_recognised_and_a_carrier_s_is_not(tmp_path):
    gen, real = tmp_path / "AWB_X.pdf", tmp_path / "real.pdf"
    gen.write_bytes(ps.build_awb_pdf(SHIPMENTS[0]))
    real.write_bytes(_pdf("AIR WAYBILL 176-23418102"))
    assert ps.is_generated_awb(gen) is True
    assert ps.is_generated_awb(real) is False
    assert ps.is_generated_awb(tmp_path / "missing.pdf") is False


def test_a_date_the_model_made_up_off_a_generated_waybill_is_emptied(db_session, tmp_path):
    """JOB-60D883: MAWB Date came back '22-HAN-2023', built from the file number and the origin."""
    from app.api.v1.jobs import _blank_dates_on_generated_waybills

    job, f = _dated_job(db_session, tmp_path, generated=True)
    assert sorted(_blank_dates_on_generated_waybills(db_session, job)) == ["HBL Date", "MAWB Date"]
    assert f["MAWB Date"].extracted_value == "" and f["HBL Date"].extracted_value == ""
    assert f["Invoice Due Date"].extracted_value == "09-Nov-2026"     # reads the invoice
    assert f["Any Date"].extracted_value == "09-Nov-2026"             # reads the invoice too
    assert f["MAWB Number"].extracted_value == "176-23418102"         # not a date field


def test_a_real_waybill_keeps_its_dates(db_session, tmp_path):
    from app.api.v1.jobs import _blank_dates_on_generated_waybills

    job, f = _dated_job(db_session, tmp_path, generated=False)
    assert _blank_dates_on_generated_waybills(db_session, job) == []
    assert f["MAWB Date"].extracted_value == "22-HAN-2023" and f["HBL Date"].extracted_value == "11-Nov-2026"


def test_a_date_the_operator_typed_is_not_touched(db_session, tmp_path):
    from app.api.v1.jobs import _blank_dates_on_generated_waybills

    job, f = _dated_job(db_session, tmp_path, generated=True)
    f["MAWB Date"].corrected_value = "12-Nov-2026"
    db_session.commit()
    _blank_dates_on_generated_waybills(db_session, job)
    assert f["MAWB Date"].corrected_value == "12-Nov-2026"
