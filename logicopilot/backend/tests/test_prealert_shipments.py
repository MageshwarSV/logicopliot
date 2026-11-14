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


def test_a_single_shipment_mail_is_left_alone(monkeypatch):
    called = []
    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: called.append(1) or [])
    subject = "FW: Pre-Alert: Juniper //45T0523243"
    assert ps.plan_split(_mail(subject), subject, "body", list(FILES)) is None
    assert called == []                                            # the model was not even asked


def test_several_numbers_in_the_subject_but_one_readable_shipment_is_not_split(monkeypatch):
    """A subject that merely LOOKS like three shipments must never become three empty jobs."""
    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [dict(SHIPMENTS[0])])
    assert ps.plan_split(_mail(), SUBJECT, "body", list(FILES)) is None
