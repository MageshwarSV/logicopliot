"""Excel by sheet (TemplateGroup.excel_mode = "sheet"): each sheet is read from its cells,
classified on its own, placed in a slot or sent to Unclassified, and extracted from the cells.
A "legacy" template (every existing one) keeps converting Excel to PDF exactly as before.
Offline: the AI and OCR are stand-ins."""
import io
from email.message import EmailMessage
from unittest.mock import patch

import fitz
import openpyxl

from app.api.v1.jobs import run_extraction
from app.core.email_puller import _mailbox_setup, _process_one_message
from app.core.excel_intake import document_number, same_as_pdf
from app.core.excel_text import read_workbook
from app.core.system_settings import set_extraction_engine
from app.models.billable_page_event import BillablePageEvent
from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, UnclassifiedFile
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import make_tenant


def _xlsx(rows: list[list], title="Sheet1") -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = title
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _pdf(text: str) -> bytes:
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


INVOICE_ROWS = [["Invoice No", "MES2620601085"], [], ["Part", "Qty"], ["K1", 10], ["K2", 20]]
PACKING_ROWS = [["Packing List No", "PL-77001"], ["Carton", "Weight"], ["1", 12.5]]
DSR_ROWS = [["DSR Header"], ["x", "y"]]


# ---- the duplicate check, on its own ---------------------------------------------------------

def test_the_document_number_is_read_beside_or_below_its_label():
    beside = read_workbook(_xlsx(INVOICE_ROWS), "a.xlsx").sheets[0]
    below = read_workbook(_xlsx([["Invoice Number"], ["INV 95035026"]]), "b.xlsx").sheets[0]
    none = read_workbook(_xlsx([["Part", "Qty"]]), "c.xlsx").sheets[0]
    assert document_number(beside) == "MES2620601085"
    assert document_number(below) == "INV 95035026"
    assert document_number(none) is None


def test_same_as_pdf_ignores_spacing_and_case_but_not_short_numbers():
    assert same_as_pdf("INV 95035026", ["... invoice no: inv-95035026 dated ..."])
    assert not same_as_pdf("INV 95035027", ["invoice no inv-95035026"])
    assert not same_as_pdf("12", ["12 cartons"])                 # too short to trust
    assert not same_as_pdf(None, ["anything"])


# ---- through the real email flow -------------------------------------------------------------

def _template(db_session, excel_mode: str):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="ready", mode="Air Import",
                          excel_mode=excel_mode)
    db_session.add(group)
    db_session.flush()
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice", order_index=0)
    pl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List", doc_type="Packing List", order_index=1)
    db_session.add_all([inv, pl])
    db_session.add(CustomField(tenant_id=tenant.id, group_id=group.id, label_name="consignee",
                               kind="hardcoded", hardcoded_value="Test Consignee Co"))
    db_session.commit()
    ctx, early = _mailbox_setup(db_session, tenant.id, forced_group_id=group.id)
    assert early is None
    return group, inv, pl, ctx


def _mail(attachments: list[tuple[str, bytes]]) -> bytes:
    msg = EmailMessage()
    msg["Message-ID"] = f"<excel-{len(attachments)}-{attachments[0][0]}@example.com>"
    msg["From"] = "agent@example.com"
    msg["Subject"] = "SINA2626873 documents"
    msg.set_content("Please find attached.")
    for name, data in attachments:
        sub = ("pdf" if name.endswith(".pdf") else "zip" if name.endswith(".zip")
               else "vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        msg.add_attachment(data, maintype="application", subtype=sub, filename=name)
    return msg.as_bytes()


def _run(db_session, ctx, raw, pdf_key, sheet_keys: dict[str, str], converter=None):
    """PDFs all go to `pdf_key`; a sheet goes where its file name says (sheet_keys), else nowhere."""
    sheet_calls: list[str] = []

    def _assign(prepared, cand_slots, **_kw):
        return [[{"key": pdf_key, "pages": [1], "evidence": "invoice"}] for _ in prepared]

    def _classify(label, text, image, candidates):
        sheet_calls.append(label)
        for marker, key in sheet_keys.items():
            if marker in label:
                return [{"key": key, "pages": [1], "evidence": f"looks like {marker}", "source": "model"}]
        return []

    patches = [
        patch("app.core.docai.ocr_page_image", return_value={"text": "Invoice no MES2620601085", "tokens": []}),
        patch("app.core.classifier_air_import.assign_documents_detailed", side_effect=_assign),
        patch("app.core.classifier_air_import.classify_document", side_effect=_classify),
        patch("app.api.v1.jobs.run_extraction"),
    ]
    if converter is not None:
        patches.append(patch("app.core.office_convert.convert_excel_sheets_to_pdfs", side_effect=converter))
    for p in patches:
        p.start()
    try:
        result = _process_one_message(
            db_session, b"1", raw, ctx, mail_email=None, mail_app_password=None, mail_host=None,
            extraction_engine="gpt5_mini_vision", vision_engine_model="gpt-5-mini",
            custom_filter_texts=[], reexamine=False,
        )
    finally:
        for p in patches:
            p.stop()
    return result, sheet_calls


def test_sheet_mode_places_parks_and_bills_each_sheet(db_session):
    group, inv, pl, ctx = _template(db_session, "sheet")
    raw = _mail([
        ("invoice.pdf", _pdf("Invoice no MES2620601085")),
        ("inv-copy.xlsx", _xlsx(INVOICE_ROWS)),                    # same invoice as the PDF
        ("pl-only.xlsx", _xlsx(PACKING_ROWS)),                     # packing list only as Excel
        ("DSRImportTemplate20261112.xlsx", _xlsx(DSR_ROWS)),       # on the ignore list
        ("summary.xlsx", _xlsx([["Shipment", "Status"], ["K2704", "shipped"]])),   # not a document
    ])
    result, sheet_calls = _run(db_session, ctx, raw, inv.id, {"inv-copy": inv.id, "pl-only": pl.id})
    assert result and result.get("job_id"), result
    job = db_session.get(Job, result["job_id"])

    # the ignore-list file never reaches the AI
    assert not any("DSRImportTemplate" in c for c in sheet_calls)

    docs = db_session.query(JobDocument).filter(JobDocument.job_id == job.id, JobDocument.file_path.isnot(None)).all()
    by_slot = {(d.template_document_id, d.source_kind) for d in docs}
    assert (inv.id, "pdf") in by_slot                              # the PDF invoice is kept
    assert (pl.id, "excel") in by_slot                             # Excel-only packing list placed
    assert (inv.id, "excel") not in by_slot                        # the Excel copy is not doubled
    excel_doc = next(d for d in docs if d.source_kind == "excel")
    assert excel_doc.sheet_name == "Sheet1" and excel_doc.page_count == 0

    parked = {u.original_filename: u.sheets for u in
              db_session.query(UnclassifiedFile).filter(UnclassifiedFile.job_id == job.id).all()}
    assert parked["inv-copy.xlsx"][0]["reason"] == "PDF version used: MES2620601085"
    assert parked["DSRImportTemplate20261112.xlsx"][0]["reason"] == "on the ignore list"
    assert parked["summary.xlsx"][0]["reason"] == "not recognised as a customs document"

    # billing: 1 PDF page + Excel at intake: inv-copy 4 rows=1, pl 3 rows=1, summary 2 rows=1,
    # DSR ignore list = 1 per sheet -> 1 + 4
    billed = sum(e.page_count for e in db_session.query(BillablePageEvent).filter(BillablePageEvent.job_id == job.id))
    assert billed == 5


def test_a_mail_with_only_excel_still_becomes_a_job_in_sheet_mode(db_session):
    group, inv, pl, ctx = _template(db_session, "sheet")
    raw = _mail([("pl-only.xlsx", _xlsx(PACKING_ROWS))])
    result, _ = _run(db_session, ctx, raw, inv.id, {"pl-only": pl.id})
    assert result and result.get("job_id"), result
    assert result["filled_slots"] == ["Packing List"]


def test_legacy_mode_converts_excel_to_pdf_exactly_as_before(db_session):
    group, inv, pl, ctx = _template(db_session, "legacy")
    converted: list[str] = []

    def _convert(src, out_dir, work_dir):
        converted.append(src.suffix)
        pdf = out_dir / "sheet.pdf"
        pdf.write_bytes(_pdf("Invoice no MES2620601085"))
        return [("Sheet1", pdf)]

    # Legacy only ever converted Excel that came inside a zip (a directly attached Excel file is
    # not picked up at all there) - unchanged.
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("inv-copy.xlsx", _xlsx(INVOICE_ROWS))
    raw = _mail([("invoice.pdf", _pdf("x")), ("docs.zip", buf.getvalue())])
    result, sheet_calls = _run(db_session, ctx, raw, inv.id, {"inv-copy": inv.id}, converter=_convert)
    assert result and result.get("job_id"), result
    assert converted == [".xlsx"]                                  # converted, as before
    assert sheet_calls == []                                       # no per-sheet reading
    job = db_session.get(Job, result["job_id"])
    assert db_session.query(UnclassifiedFile).filter(UnclassifiedFile.job_id == job.id).count() == 0
    assert all(d.source_kind == "pdf" for d in db_session.query(JobDocument).filter(JobDocument.job_id == job.id))


# ---- extraction reads the cells, never images -------------------------------------------------

def test_an_excel_document_is_extracted_from_its_cells_never_by_vision(db_session, tmp_path):
    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="approved", excel_mode="sheet")
    db_session.add(group)
    db_session.commit()
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add(inv)
    db_session.commit()
    db_session.add(FieldMark(tenant_id=tenant.id, document_id=inv.id, label_name="Invoice No",
                             page_number=1, x=0.1, y=0.1, width=0.1, height=0.1))
    db_session.add(FieldMark(tenant_id=tenant.id, document_id=inv.id, label_name="Part",
                             page_number=1, x=0.1, y=0.3, width=0.1, height=0.1, is_multi_value=True))
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-XLSX", status="extracting")
    db_session.add(job)
    db_session.commit()
    path = tmp_path / "inv.xlsx"
    path.write_bytes(_xlsx(INVOICE_ROWS, title="Detail"))
    db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=inv.id,
                               file_path=str(path), page_count=0, source_kind="excel", sheet_name="Detail"))
    db_session.commit()

    seen: list[str] = []
    set_extraction_engine(db_session, "gpt5_mini_vision")          # vision engine on - still text
    with patch("app.api.v1.jobs.extract_document_fields",
               side_effect=lambda text, fields, **k: (seen.append(text) or {"Invoice No": "MES2620601085"})), \
         patch("app.api.v1.jobs.extract_document_rows",
               side_effect=lambda text, specs, *a, **k: (seen.append(text) or [{"Part": "K1"}, {"Part": "K2"}])), \
         patch("app.api.v1.jobs.extract_document_fields_from_images", side_effect=AssertionError("vision used")), \
         patch("app.api.v1.jobs.extract_document_rows_from_images", side_effect=AssertionError("vision used")), \
         patch("app.api.v1.jobs.get_page_ocr", side_effect=AssertionError("OCR used")):
        run_extraction(db_session, job)

    assert seen and all("R1 | A: Invoice No | B: MES2620601085" in t for t in seen)
    db_session.refresh(job)
    assert job.status in ("extracted", "possible_duplicate")


# ---- Smart Upload (its core, called directly - same as the endpoint after unzipping) ---------

def test_smart_upload_in_sheet_mode_places_excel_and_never_converts_it(db_session):
    from app.api.v1.jobs import _classify_and_place_files

    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="ready", mode="Air Import",
                          excel_mode="sheet")
    db_session.add(group)
    db_session.flush()
    pl = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Packing List", doc_type="Packing List")
    db_session.add(pl)
    db_session.flush()
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-SU-XLSX", status="draft")
    db_session.add(job)
    db_session.flush()
    db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=pl.id, page_count=0))
    db_session.commit()
    candidates = [{"key": pl.id, "name": "Packing List", "doc_type": "Packing List", "fields": []}]

    with patch("app.core.classifier_air_import.classify_document",
               side_effect=lambda label, *a, **k: [{"key": pl.id, "pages": [1], "evidence": "packing", "source": "model"}]
               if "pl-only" in label else []), \
         patch("app.core.office_convert.convert_excel_sheets_to_pdfs", side_effect=AssertionError("converted")), \
         patch("app.api.v1.jobs._maybe_auto_extract"):
        out = _classify_and_place_files(db_session, job, group, candidates, "gpt5_mini_vision", "gpt-5-mini",
                                        [("pl-only.xlsx", _xlsx(PACKING_ROWS)),
                                         ("DSRImportTemplate1.xlsx", _xlsx(DSR_ROWS))])

    by_name = {r["filename"]: r for r in out["results"]}
    assert by_name["pl-only.xlsx"]["matched"] == ["Packing List"]
    assert by_name["DSRImportTemplate1.xlsx"]["matched"] is None
    doc = db_session.query(JobDocument).filter(JobDocument.job_id == job.id).one()
    assert doc.source_kind == "excel" and doc.file_path
    assert db_session.query(UnclassifiedFile).filter(UnclassifiedFile.job_id == job.id).count() == 1


# ---- operator actions on Unclassified Excel (endpoint functions called directly) -------------

def test_an_operator_can_assign_one_sheet_then_delete_the_rest(db_session, tmp_path, monkeypatch):
    from app.api.v1 import jobs as jobs_api
    from app.core.deps import TenantScope

    tenant = make_tenant(db_session)
    group = TemplateGroup(tenant_id=tenant.id, name="Air Import", status="ready", excel_mode="sheet")
    db_session.add(group)
    db_session.flush()
    inv = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name="Invoice", doc_type="Invoice")
    db_session.add(inv)
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-ASSIGN-XLSX", status="draft")
    db_session.add(job)
    db_session.flush()
    db_session.add(JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=inv.id, page_count=0))
    wb = openpyxl.Workbook()
    wb.active.title = "ProForma"
    wb.active.append(["Invoice No", "PF-1001"])
    wb.create_sheet("Cartons").append(["Carton", "1"])
    buf = io.BytesIO()
    wb.save(buf)
    path = tmp_path / "PSDR.xlsx"
    path.write_bytes(buf.getvalue())
    uf = UnclassifiedFile(tenant_id=tenant.id, job_id=job.id, original_filename="PSDR.xlsx", file_path=str(path),
                          sheets=[{"name": "ProForma", "rows": 1, "reason": "x"}, {"name": "Cartons", "rows": 1, "reason": "x"}])
    db_session.add(uf)
    db_session.commit()
    monkeypatch.setattr(jobs_api, "_maybe_auto_extract", lambda *a, **k: None)
    scope = TenantScope(tenant_id=tenant.id, is_super_admin=False)

    listed = jobs_api.list_unclassified_files(job.id, db=db_session, scope=scope, user=None)
    assert [s.name for s in listed[0].sheets] == ["ProForma", "Cartons"]
    preview = jobs_api.preview_unclassified_file(job.id, uf.id, db=db_session, scope=scope, user=None)
    assert preview[0]["rows"][0]["cells"] == {"A": "Invoice No", "B": "PF-1001"}

    jobs_api.assign_unclassified_file(job.id, uf.id, jobs_api.AssignUnclassifiedFilePayload(
        template_document_id=inv.id, sheet="ProForma"), db=db_session, scope=scope, user=None)
    doc = db_session.query(JobDocument).filter(JobDocument.job_id == job.id).one()
    assert doc.source_kind == "excel" and doc.sheet_name == "ProForma"
    db_session.refresh(uf)
    assert [s["name"] for s in uf.sheets] == ["Cartons"]           # only the unassigned one left
    sheet = jobs_api.get_document_sheet(job.id, doc.id, db=db_session, scope=scope, user=None)
    assert sheet["name"] == "ProForma"

    jobs_api.delete_unclassified_file(job.id, uf.id, sheet=None, db=db_session, scope=scope, user=None)
    assert db_session.query(UnclassifiedFile).filter(UnclassifiedFile.job_id == job.id).count() == 0
    assert not path.exists()
