"""Every message gets its own scratch folder - two shipments split from one pre-alert, worked on
at the same time, must never share one (production, 17 Nov 2026: JOB-070575 crashed with
"cannot save with zero pages" and JOB-70EF19 lost its real Air Waybill, because both shipments'
Message-IDs were cut at 60 characters to the same folder name)."""
import email

from app.core import prealert_shipments as ps
from app.core.email_puller import _scratch_dir_name
from tests.test_prealert_shipments import EXPEDITORS_SUBJECT, SHIPMENTS, _mail, _pdf

# The real ids from production.
PARENT = "<1a1ea1dfd1d.27241061450400.166963104195987106@4slogistics.com>"
CHILD_1 = "<1a1ea1dfd1d.27241061450400.166963104195987106@4slogistics.com.45T0524120>"
CHILD_2 = "<1a1ea1dfd1d.27241061450400.166963104195987106@4slogistics.com.45T0523984>"


def _old_name(mid: str) -> str:
    return "".join(ch for ch in mid if ch.isalnum() or ch in "-_")[:60] or "msg"


def test_the_old_naming_really_collided():
    assert _old_name(CHILD_1) == _old_name(CHILD_2)


def test_split_shipments_get_their_own_folders():
    names = {_scratch_dir_name(m) for m in (PARENT, CHILD_1, CHILD_2)}
    assert len(names) == 3


def test_the_name_is_stable_safe_and_readable():
    name = _scratch_dir_name(CHILD_1)
    assert name == _scratch_dir_name(CHILD_1)                      # same mail, same folder
    assert all(ch.isalnum() or ch in "-_" for ch in name)          # no path characters
    assert name.startswith("1a1ea1dfd1d27241061450400") and len(name) <= 53
    assert _scratch_dir_name("") .startswith("msg-")


def test_every_mail_a_long_id_pre_alert_splits_into_has_its_own_folder(monkeypatch):
    shipments = [{**SHIPMENTS[0], "hawb": "45T0524120"}, {**SHIPMENTS[1], "hawb": "45T0523984"}]
    files = [("22H0523553.pdf", _pdf("Commercial Invoice")), ("22H0523560.pdf", _pdf("Commercial Invoice"))]
    monkeypatch.setattr(ps, "read_shipments", lambda *a, **k: [dict(x) for x in shipments])
    msg = _mail(EXPEDITORS_SUBJECT, files)
    msg.replace_header("Message-ID", "<" + "A" * 120 + "@EXCHANGE.namprd08.prod.outlook.com>")
    subs = ps.plan_split(msg, EXPEDITORS_SUBJECT, "body", list(files))
    mids = [email.message_from_bytes(raw)["Message-ID"] for raw in subs]
    assert len(set(mids)) == 2
    assert len({_scratch_dir_name(m) for m in mids}) == 2
