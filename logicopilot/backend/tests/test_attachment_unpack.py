"""attachment_unpack: every readable file comes out of a zip - every folder, and zips inside
zips - while an ordinary mail comes out exactly as before. Modelled on the real mail
"312-93004472 - SINA2626873 (DTA).zip" (Nov 2026): 13 sub-HAWB folders, invoices and packing
lists in inner zips, one inner zip password-protected."""
import email
import io
import zipfile
from email.message import EmailMessage
from unittest.mock import patch

import fitz

from app.core import attachment_unpack as au
from app.core.attachment_unpack import unpack_attachments
from app.core.email_puller import CLASSIFY_EXTS, _mailbox_setup, _process_one_message
from app.core.office_convert import OFFICE_EXTS
from app.models.custom_field import CustomField
from app.models.job import Job
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant

ACCEPT = CLASSIFY_EXTS | OFFICE_EXTS


def _pdf(text: str = "page") -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _zip(entries: dict[str, bytes], encrypted: set[str] = frozenset()) -> bytes:
    """A zip in memory. Names in `encrypted` are flagged as password-protected (the flag is all
    the reader looks at - opening one without a password raises, exactly like a real one)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries.items():
            z.writestr(name, data)
    raw = bytearray(buf.getvalue())
    # Python's writer always clears the "encrypted" bit, so set it in the central directory
    # (general-purpose flag, offset 8 of each "PK\x01\x02" record) - which is what a reader
    # checks before refusing to open the entry without a password.
    pos = raw.find(b"PK\x01\x02")
    while pos != -1:
        name_len = int.from_bytes(raw[pos + 28:pos + 30], "little")
        name = raw[pos + 46:pos + 46 + name_len].decode()
        if name in encrypted:
            raw[pos + 8] |= 0x1
        pos = raw.find(b"PK\x01\x02", pos + 46 + name_len)
    return bytes(raw)


def _names(result):
    return [n for n, _ in result.files]


# ---- an ordinary mail must come out exactly as before ----------------------------------------

def test_plain_attachments_pass_through_untouched():
    a, b = _pdf("a"), b"xlsx-bytes"
    res = unpack_attachments([("HAWB.pdf", a), ("Invoice.xlsx", b)], ACCEPT)
    assert res.files == [("HAWB.pdf", a), ("Invoice.xlsx", b)]
    assert res.notes() is None


def test_a_flat_zip_gives_the_same_names_and_order_as_before():
    z = _zip({"inv.pdf": _pdf("1"), "pl.pdf": _pdf("2"), "sheet.xls": b"x"})
    res = unpack_attachments([("docs.zip", z)], ACCEPT)
    assert _names(res) == ["inv.pdf", "pl.pdf", "sheet.xls"]
    assert res.notes() is None


def test_no_limit_applies_to_an_ordinary_attachment(monkeypatch):
    monkeypatch.setattr(au, "MAX_ENTRY_BYTES", 10)
    big = _pdf("big")
    assert unpack_attachments([("big.pdf", big)], ACCEPT).files == [("big.pdf", big)]


# ---- what was missed before ---------------------------------------------------------------

def test_files_in_sub_folders_are_read_with_their_bare_names():
    z = _zip({"SINA2626873A - WT/INV 95035026.pdf": _pdf(), "SINA2626873A - WT/PCK 95035026.pdf": _pdf()})
    assert _names(unpack_attachments([("mail.zip", z)], ACCEPT)) == ["INV 95035026.pdf", "PCK 95035026.pdf"]


def test_a_zip_inside_a_sub_folder_is_opened():
    inner = _zip({"MES2620601087_INV.pdf": _pdf(), "MES2620601087_PCK.pdf": _pdf()})
    outer = _zip({"SINA2626873E - MURATA/SINA2626873E.pdf": _pdf(),
                  "SINA2626873E - MURATA/k2704-inv.zip": inner})
    assert _names(unpack_attachments([("mail.zip", outer)], ACCEPT)) == [
        "SINA2626873E.pdf", "MES2620601087_INV.pdf", "MES2620601087_PCK.pdf"]


def test_zips_nested_three_deep_are_all_opened():
    z3 = _zip({"deep.pdf": _pdf()})
    z2 = _zip({"c/level3.zip": z3, "mid.pdf": _pdf()})
    z1 = _zip({"b/level2.zip": z2})
    assert sorted(_names(unpack_attachments([("a.zip", z1)], ACCEPT))) == ["deep.pdf", "mid.pdf"]


def test_nesting_past_the_limit_is_listed_not_read():
    z = _zip({"x.pdf": _pdf()})
    for _ in range(au.MAX_DEPTH):
        z = _zip({"inner.zip": z})
    res = unpack_attachments([("top.zip", z)], ACCEPT)
    assert res.files == []
    assert "levels deep" in res.skipped[0]["reason"]


# ---- password-protected, damaged, junk, limits ----------------------------------------------

def test_a_password_protected_inner_zip_is_listed_with_its_file_names_and_does_not_crash():
    locked = _zip({"MAL_IV.pdf": _pdf(), "MAL_PL.pdf": _pdf()}, encrypted={"MAL_IV.pdf", "MAL_PL.pdf"})
    outer = _zip({"SINA2626873D - TAIYO/SINA2626873D.pdf": _pdf(),
                  "SINA2626873D - TAIYO/FLXINAUTO.zip": locked})
    res = unpack_attachments([("mail.zip", outer)], ACCEPT)
    assert _names(res) == ["SINA2626873D.pdf"]
    assert res.locked == [{"path": "mail.zip/SINA2626873D - TAIYO/FLXINAUTO.zip",
                           "files": ["MAL_IV.pdf", "MAL_PL.pdf"]}]
    assert "password-protected" in res.summary()


def test_a_password_protected_outer_zip_does_not_crash():
    z = _zip({"a.pdf": _pdf()}, encrypted={"a.pdf"})
    res = unpack_attachments([("locked.zip", z)], ACCEPT)
    assert res.files == [] and res.locked[0]["files"] == ["a.pdf"]


def test_a_damaged_inner_zip_is_listed_and_the_rest_still_read():
    outer = _zip({"good.pdf": _pdf(), "folder/broken.zip": b"not a zip"})
    res = unpack_attachments([("mail.zip", outer)], ACCEPT)
    assert _names(res) == ["good.pdf"]
    assert res.skipped == [{"path": "mail.zip/folder/broken.zip", "reason": "damaged zip - could not be opened"}]


def test_a_damaged_outer_zip_is_listed():
    res = unpack_attachments([("bad.zip", b"nope")], ACCEPT)
    assert res.files == [] and res.skipped[0]["reason"].startswith("damaged")


def test_mac_and_windows_junk_is_ignored_silently():
    z = _zip({"__MACOSX/._inv.pdf": b"junk", "folder/._pl.pdf": b"junk", "folder/.DS_Store": b"x",
              "Thumbs.db": b"x", "inv.pdf": _pdf()})
    res = unpack_attachments([("mail.zip", z)], ACCEPT)
    assert _names(res) == ["inv.pdf"] and res.notes() is None


def test_unsupported_types_are_listed_but_signature_images_are_not():
    z = _zip({"notes.txt": b"x", "more.rar": b"x", "logo.gif": b"x", "inv.pdf": _pdf()})
    res = unpack_attachments([("mail.zip", z), ("image001.gif", b"x"), ("readme.txt", b"x")], ACCEPT)
    assert sorted(s["path"] for s in res.skipped) == ["mail.zip/more.rar", "mail.zip/notes.txt", "readme.txt"]


def test_file_count_and_size_limits(monkeypatch):
    monkeypatch.setattr(au, "MAX_FILES", 2)
    z = _zip({f"{i}.pdf": _pdf(str(i)) for i in range(4)})
    res = unpack_attachments([("many.zip", z)], ACCEPT)
    assert len(res.files) == 2 and len(res.skipped) == 2
    monkeypatch.setattr(au, "MAX_FILES", 300)
    monkeypatch.setattr(au, "MAX_ENTRY_BYTES", 10)
    res = unpack_attachments([("big.zip", _zip({"big.pdf": _pdf()}))], ACCEPT)
    assert res.files == [] and "larger than" in res.skipped[0]["reason"]


def test_a_zip_bomb_entry_is_not_opened():
    z = _zip({"bomb.pdf": b"\0" * (5 * 1024 * 1024)})      # 5 MB of zeros packs to a few KB
    res = unpack_attachments([("b.zip", z)], ACCEPT)
    assert res.files == [] and "suspicious" in res.skipped[0]["reason"]


# ---- through the real email flow --------------------------------------------------------------

def test_an_email_with_a_nested_and_a_locked_zip_reads_everything_it_can(db_session):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="ready")
    db_session.add(group)
    db_session.flush()
    doc = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice", order_index=0)
    db_session.add(doc)
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Test Consignee Co"))
    db_session.commit()
    ctx, early = _mailbox_setup(db_session, tenant.id, forced_group_id=group.id)
    assert early is None

    inner = _zip({"MES_INV.pdf": _pdf("invoice")})
    locked = _zip({"MAL_IV.pdf": _pdf()}, encrypted={"MAL_IV.pdf"})
    outer = _zip({"SINA2626873E - MURATA/SINA2626873E.pdf": _pdf("hawb"),
                  "SINA2626873E - MURATA/k2704-inv.zip": inner,
                  "SINA2626873D - TAIYO/FLXINAUTO.zip": locked})
    msg = EmailMessage()
    msg["Message-ID"] = "<nested-zip-1@example.com>"
    msg["From"] = "agent@example.com"
    msg["Subject"] = "312-93004472 - SINA2626873"
    msg.set_content("Please find attached.")
    msg.add_attachment(outer, maintype="application", subtype="zip", filename="mail.zip")

    seen: list[str] = []

    def _classify(prepared, cand_slots, **_kw):
        seen.extend(p["name"] for p in prepared)
        return [[{"key": doc.id, "pages": [1], "evidence": "invoice"}] for _ in prepared]

    with patch("app.core.docai.ocr_page_image", return_value={"text": "invoice", "tokens": []}), \
         patch("app.core.classifier_sea_import.assign_documents_detailed", side_effect=_classify), \
         patch("app.core.classifier_air_import.assign_documents_detailed", side_effect=_classify), \
         patch("app.api.v1.jobs.run_extraction"):
        result = _process_one_message(
            db_session, b"1", msg.as_bytes(), ctx,
            mail_email=None, mail_app_password=None, mail_host=None,
            extraction_engine="ocr_gpt4o_mini", vision_engine_model="gpt-5-mini",
            custom_filter_texts=[], reexamine=False,
        )

    # A locked zip no saved password opens: the whole job is HELD - nothing is read until the
    # password is given (agreed 2026-11-19; see tests/test_zip_passwords.py for the reading).
    assert result and result.get("job_id") and result.get("held"), result
    assert seen == []
    job = db_session.get(Job, result["job_id"])
    assert job.attachment_notes["locked"] == [
        {"path": "mail.zip/SINA2626873D - TAIYO/FLXINAUTO.zip", "files": ["MAL_IV.pdf"]}]

    # ... and "Process without locked files" reads everything else, nested zip included.
    with patch("app.core.docai.ocr_page_image", return_value={"text": "invoice", "tokens": []}), \
         patch("app.core.classifier_sea_import.assign_documents_detailed", side_effect=_classify), \
         patch("app.core.classifier_air_import.assign_documents_detailed", side_effect=_classify), \
         patch("app.api.v1.jobs.run_extraction"):
        _process_one_message(
            db_session, b"1", msg.as_bytes(), ctx, mail_email=None, mail_app_password=None, mail_host=None,
            extraction_engine="ocr_gpt4o_mini", vision_engine_model="gpt-5-mini",
            custom_filter_texts=[], reexamine=False, resume_job_id=job.id, skip_locked=True)
    assert seen == ["SINA2626873E.pdf", "MES_INV.pdf"]              # the inner zip's PDF is read


# ---- saved passwords (test passwords only) ----------------------------------------------------

import os

LOCKED_ZIPCRYPTO = os.path.join(os.path.dirname(__file__), "fixtures", "locked_zipcrypto_test-pass-2.zip")
# The classic zip encryption (what Flex's supplier zips use), made with `zip -P test-pass-2`.


def _aes_zip(entries: dict[str, bytes], password: str) -> bytes:
    import pyzipper
    buf = io.BytesIO()
    with pyzipper.AESZipFile(buf, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
        z.setpassword(password.encode())
        for name, data in entries.items():
            z.writestr(name, data)
    return buf.getvalue()


def test_a_classic_locked_zip_opens_with_the_second_saved_password():
    locked = open(LOCKED_ZIPCRYPTO, "rb").read()
    outer = _zip({"SINA2626873D - TAIYO/FLXINAUTO.zip": locked, "a.pdf": _pdf()})
    res = unpack_attachments([("mail.zip", outer)], ACCEPT, passwords=["test-pass-1", "test-pass-2"])
    assert sorted(_names(res)) == ["MAL_IV.pdf", "MAL_PL.pdf", "a.pdf"]
    assert res.locked == [] and res.used_passwords == ["test-pass-2"]
    assert res.unlocked == ["mail.zip/SINA2626873D - TAIYO/FLXINAUTO.zip"]


def test_wrong_passwords_leave_it_locked():
    locked = open(LOCKED_ZIPCRYPTO, "rb").read()
    res = unpack_attachments([("FLXINAUTO.zip", locked)], ACCEPT, passwords=["nope", "also-wrong"])
    assert res.files == [] and res.locked[0]["files"] == ["MAL_IV.pdf", "MAL_PL.pdf"]
    assert res.used_passwords == []


def test_an_aes_locked_zip_opens_too():
    z = _aes_zip({"inv.pdf": _pdf("aes")}, "test-aes-pw")
    res = unpack_attachments([("aes.zip", z)], ACCEPT, passwords=["test-aes-pw"])
    assert _names(res) == ["inv.pdf"] and res.locked == []


def test_no_passwords_behaves_exactly_as_before():
    locked = open(LOCKED_ZIPCRYPTO, "rb").read()
    res = unpack_attachments([("FLXINAUTO.zip", locked)], ACCEPT)
    assert res.files == [] and len(res.locked) == 1
