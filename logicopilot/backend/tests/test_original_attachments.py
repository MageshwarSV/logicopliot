"""Reclassify must re-read the attachments AS THEY ARRIVED, not the job's placed documents.

Re-feeding the slots cannot work for a combined attachment. One "Commercial Invoice & Packing
List" PDF legitimately fills two slots, so each slot holds its own copy of it under the SAME
original name - and handing both back to the classifier is handing it the same document twice.
They merge into one document carrying every invoice twice (stored as "<name> +1 more"), and
each further reclassify compounds it. Production JOB-CB9B46 ended up with a 6-page Invoice
reading 8667395, 8667396, 8667395, T&C, 8667396, T&C after exactly that.

app.core.job_email.save_original_email already keeps the as-received attachments for every
job created from an email; original_attachments reads them back.
"""
import json

from app.core.job_email import job_email_dir, original_attachments, save_original_email


def test_the_attachments_come_back_exactly_as_they_were_saved():
    job_id = "job-orig-1"
    files = [("[20261023] Commercial Invoice 380.PDF", b"%PDF-1.4 invoice bytes"),
             ("[20261023] Packing List 271.PDF", b"%PDF-1.4 packing bytes")]
    save_original_email(job_id, None, files, sender="s@x.com", subject="docs")

    got = original_attachments(job_id)
    assert got == files, "name and bytes must both survive the round trip"


def test_a_job_with_no_email_behind_it_returns_nothing():
    """An Excel-entry or hand-uploaded job has no email copy. That is "not applicable", not an
    error - the caller falls back to the documents in the slots."""
    assert original_attachments("job-that-never-existed") == []


def test_a_half_written_copy_never_raises():
    """A missing meta.json (or a malformed one) must cost the fallback, never the request."""
    job_id = "job-orig-broken"
    save_original_email(job_id, None, [("a.pdf", b"x")], sender="s@x.com", subject="d")
    (job_email_dir(job_id) / "meta.json").write_text("{not json", encoding="utf-8")
    assert original_attachments(job_id) == []


def test_an_attachment_whose_file_is_gone_is_skipped_not_fatal():
    job_id = "job-orig-missing-file"
    save_original_email(job_id, None, [("a.pdf", b"aaa"), ("b.pdf", b"bbb")],
                        sender="s@x.com", subject="d")
    meta = json.loads((job_email_dir(job_id) / "meta.json").read_text(encoding="utf-8"))
    (job_email_dir(job_id) / "attachments" / meta["attachments"][0]["stored_as"]).unlink()

    got = original_attachments(job_id)
    assert got == [("b.pdf", b"bbb")], "the readable one still comes back"


def test_a_stored_name_cannot_point_outside_the_job_folder():
    """stored_as is written by this app, but it is read back off disk - a name that walks up
    out of the job's own folder must never be opened."""
    job_id = "job-orig-escape"
    save_original_email(job_id, None, [("a.pdf", b"aaa")], sender="s@x.com", subject="d")
    out = job_email_dir(job_id)
    secret = out.parent / "somebody-elses.pdf"
    secret.write_bytes(b"not yours")
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    meta["attachments"].append({"name": "sneaky.pdf", "stored_as": "../somebody-elses.pdf"})
    (out / "meta.json").write_text(json.dumps(meta), encoding="utf-8")

    got = original_attachments(job_id)
    assert all(b != b"not yours" for _, b in got), "a traversing path must be refused"
    assert got == [("a.pdf", b"aaa")], "and the legitimate attachment still comes back"


def test_two_slots_holding_the_same_source_file_is_exactly_what_this_avoids():
    """The shape of the bug, written down: a combined attachment is ONE file, however many
    slots end up holding a copy of it. Reclassify must see one, not one per slot."""
    job_id = "job-orig-combined"
    combined = ("[20261023] Commercial Invoice 380.PDF", b"%PDF-1.4 invoice and packing list")
    save_original_email(job_id, None, [combined], sender="s@x.com", subject="docs")

    got = original_attachments(job_id)
    assert len(got) == 1, (
        "the mail carried one attachment; the Invoice and Packing List slots each holding a "
        "copy of it must not turn that into two files on a re-run"
    )
    assert got[0] == combined
