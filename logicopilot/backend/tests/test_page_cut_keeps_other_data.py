"""Removing a page removes what was read from THAT page - nothing else.

Cutting one page out of an extracted document used to delete every value read from the whole
document and every calculated field on the whole job, and then read nothing again. The Excel
built afterwards went out with those cells empty: an operator removed one stray page and the
job lost its invoice number, its CTHs and its lookups.

Two rules, both from the operator who found it:
  - "if I removed the page, that page and that data only";
  - "invoice no is set to read invoice, PL and BL - if I remove the PL it must not remove the
    field".
"""
import json

import fitz

from app.models.custom_field import CustomField
from app.models.field_mark import FieldMark
from app.models.job import Job, JobDocument, JobFieldValue
from app.models.template_document import TemplateDocument
from app.models.template_group import TemplateGroup
from tests.conftest import login, make_tenant, make_user

# What each page of the three-page invoice prints. The invoice number is on every page; the
# buyer only on page 1; the remark only on page 2; one product line per page.
INVOICE_PAGES = [
    "INVOICE INV-7781 Buyer Juniper Networks WIDGET ALPHA 10 PCS 100.00",
    "INVOICE INV-7781 Remark HANDLE WITH CARE WIDGET BRAVO 20 PCS 200.00",
    "INVOICE INV-7781 WIDGET CHARLIE 30 PCS 300.00",
]
PL_PAGES = ["PACKING LIST INV-7781 Cartons 12", "PACKING LIST Net weight 55"]


def _pdf(pages: list[str]) -> bytes:
    doc = fitz.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    data = doc.tobytes()
    doc.close()
    return data


def _mark(db, tenant, tdoc, label, multi=False):
    m = FieldMark(tenant_id=tenant.id, document_id=tdoc.id, label_name=label, page_number=1,
                  x=0.1, y=0.1, width=0.1, height=0.05)
    if multi and hasattr(m, "multi_value"):
        m.multi_value = True
    db.add(m)
    db.flush()
    return m


def _file(db, tenant, job, tdoc, pages, name, ocr=True):
    from app.api.v1.jobs import _job_doc_dir, _render_pages

    jd = JobDocument(tenant_id=tenant.id, job_id=job.id, template_document_id=tdoc.id,
                     original_name=name, page_count=0, set_index=1)
    db.add(jd)
    db.flush()
    ddir = _job_doc_dir(jd.id)
    ddir.mkdir(parents=True, exist_ok=True)
    src = ddir / "original.pdf"
    src.write_bytes(_pdf(pages))
    jd.file_path = str(src)
    jd.page_count = _render_pages(src, ddir / "pages")
    jd.extracted_json = {"document": tdoc.name, "doc_type": tdoc.doc_type, "file": name,
                         "page_count": len(pages), "pages": list(pages), "excluded_pages": [],
                         "text": "\n".join(pages)}
    if ocr:
        (ddir / "ocr").mkdir(exist_ok=True)
        for n, text in enumerate(pages, start=1):
            tokens = [{"text": w, "x0": 0.1, "y0": 0.1, "x1": 0.2, "y1": 0.12} for w in text.split()]
            (ddir / "ocr" / f"page_{n}.json").write_text(
                json.dumps({"text": text, "layout_text": text, "structured_text": text,
                            "table_row_counts": [], "tokens": tokens}), encoding="utf-8")
    return jd


def _value(db, tenant, job, label, value, *, jd=None, mark=None, cf=None, row=None, page=None,
           corrected=None):
    fv = JobFieldValue(tenant_id=tenant.id, job_id=job.id, label_name=label, extracted_value=value,
                       corrected_value=corrected, row_index=row, found_page=page,
                       mark_id=mark.id if mark else None,
                       custom_field_id=cf.id if cf else None,
                       job_document_id=jd.id if jd else None,
                       template_document_id=jd.template_document_id if jd else None,
                       set_index=1 if (cf is not None and row is not None) else None)
    db.add(fv)
    return fv


def _setup(db, *, ocr=True):
    tenant = make_tenant(db)
    operator = make_user(db, role="operator", tenant=tenant, email="op-cut@example.com")
    # an operator can only log in while the tenant still has an active admin
    make_user(db, role="tenant_admin", tenant=tenant, email="ta-cut@example.com")
    group = TemplateGroup(tenant_id=tenant.id, name="Air import", status="ready")
    db.add(group)
    db.flush()
    docs = {}
    for i, (name, kind) in enumerate([("Bill of lading", "BL"), ("Invoice", "Invoice"),
                                      ("Packing List", "Packing List")]):
        docs[name] = TemplateDocument(tenant_id=tenant.id, group_id=group.id, name=name,
                                      doc_type=kind, order_index=i)
        db.add(docs[name])
    db.flush()
    inv_t, pl_t, bl_t = docs["Invoice"], docs["Packing List"], docs["Bill of lading"]
    marks = {label: _mark(db, tenant, inv_t, label)
             for label in ("invoice_number", "buyer", "remark")}
    for label in ("description", "quantity", "amount"):
        marks[label] = _mark(db, tenant, inv_t, label, multi=True)
    marks["cartons"] = _mark(db, tenant, pl_t, "cartons")
    marks["net_weight"] = _mark(db, tenant, pl_t, "net_weight")
    marks["hbl"] = _mark(db, tenant, bl_t, "hbl")

    def field(label, kind, sources, per_row=False):
        cf = CustomField(tenant_id=tenant.id, group_id=group.id, label_name=label, kind=kind,
                         source_document_ids=sources, per_row=per_row)
        db.add(cf)
        db.flush()
        return cf

    fields = {
        "Invoice No": field("Invoice No", "ai", [inv_t.id, pl_t.id, bl_t.id]),
        "Care Note": field("Care Note", "ai", [inv_t.id]),
        "Buyer Clean": field("Buyer Clean", "ai", [inv_t.id]),
        "Customs House Code": field("Customs House Code", "lookup", []),
        "Bill of Entry Type": field("Bill of Entry Type", "hardcoded", []),
        "CTH": field("CTH", "lookup", [], per_row=True),
    }
    job = Job(tenant_id=tenant.id, group_id=group.id, reference="JOB-CUT", status="extracted",
              created_by_id=operator.id, assigned_operator_id=operator.id)
    db.add(job)
    db.flush()
    inv = _file(db, tenant, job, inv_t, INVOICE_PAGES, "invoice.pdf", ocr=ocr)
    pl = _file(db, tenant, job, pl_t, PL_PAGES, "pl.pdf", ocr=ocr)
    bl = _file(db, tenant, job, bl_t, ["AIR WAYBILL HAWB-55 INV-7781"], "awb.pdf", ocr=ocr)

    _value(db, tenant, job, "invoice_number", "INV-7781", jd=inv, mark=marks["invoice_number"], page=1)
    _value(db, tenant, job, "buyer", "Juniper Networks", jd=inv, mark=marks["buyer"], page=1)
    _value(db, tenant, job, "remark", "HANDLE WITH CARE", jd=inv, mark=marks["remark"], page=2)
    for row, (desc, qty, amt) in enumerate([("WIDGET ALPHA", "10", "100.00"),
                                            ("WIDGET BRAVO", "20", "200.00"),
                                            ("WIDGET CHARLIE", "30", "300.00")], start=1):
        _value(db, tenant, job, "description", desc, jd=inv, mark=marks["description"], row=row)
        _value(db, tenant, job, "quantity", qty, jd=inv, mark=marks["quantity"], row=row)
        _value(db, tenant, job, "amount", amt, jd=inv, mark=marks["amount"], row=row)
        _value(db, tenant, job, "CTH", f"8517{row}000", cf=fields["CTH"], row=row,
               corrected=("85179999" if row == 3 else None))
    _value(db, tenant, job, "cartons", "12", jd=pl, mark=marks["cartons"], page=1)
    _value(db, tenant, job, "net_weight", "55", jd=pl, mark=marks["net_weight"], page=2)
    _value(db, tenant, job, "hbl", "HAWB-55", jd=bl, mark=marks["hbl"], page=1)
    _value(db, tenant, job, "Invoice No", "INV-7781", cf=fields["Invoice No"])
    _value(db, tenant, job, "Care Note", "HANDLE WITH CARE", cf=fields["Care Note"])
    _value(db, tenant, job, "Buyer Clean", "Juniper Networks", cf=fields["Buyer Clean"])
    _value(db, tenant, job, "Customs House Code", "INBLR4", cf=fields["Customs House Code"])
    _value(db, tenant, job, "Bill of Entry Type", "P", cf=fields["Bill of Entry Type"])
    db.commit()
    return operator, job, inv, pl, bl


def _values(db, job):
    db.expire_all()
    out = {}
    for fv in db.query(JobFieldValue).filter(JobFieldValue.job_id == job.id).all():
        key = fv.label_name if fv.row_index is None else f"{fv.label_name}#{fv.row_index}"
        out[key] = fv.corrected_value if fv.corrected_value is not None else (fv.extracted_value or "")
    return out


def test_only_the_removed_pages_values_go(client, db_session):
    operator, job, inv, pl, bl = _setup(db_session)
    login(client, operator.email)

    resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{inv.id}/pages/2")
    assert resp.status_code == 200, resp.text
    v = _values(db_session, job)

    # printed only on the removed page: gone
    assert v["remark"] == ""
    assert v["Care Note"] == ""                    # reads the invoice alone, and it was on page 2
    # the removed page's product line, and its own CTH
    assert [v[f"description#{i}"] for i in (1, 2)] == ["WIDGET ALPHA", "WIDGET CHARLIE"]
    assert "description#3" not in v and "CTH#3" not in v
    assert [v[f"amount#{i}"] for i in (1, 2)] == ["100.00", "300.00"]
    # ...the CTHs follow their own products, including the one an operator typed
    assert v["CTH#1"] == "85171000"
    assert v["CTH#2"] == "85179999"

    # everything else on the same document stays
    assert v["invoice_number"] == "INV-7781"       # also printed on the pages that are left
    assert v["buyer"] == "Juniper Networks"
    assert v["Buyer Clean"] == "Juniper Networks"
    # other documents and the job's own calculated fields are untouched
    assert v["cartons"] == "12" and v["net_weight"] == "55" and v["hbl"] == "HAWB-55"
    assert v["Invoice No"] == "INV-7781"
    assert v["Customs House Code"] == "INBLR4"
    assert v["Bill of Entry Type"] == "P"


def test_the_job_is_not_left_asking_for_a_re_read(client, db_session):
    operator, job, inv, pl, bl = _setup(db_session)
    login(client, operator.email)

    body = client.delete(f"/api/v1/jobs/{job.id}/documents/{inv.id}/pages/2").json()

    assert body["status"] == "extracted"
    assert not body.get("needs_reextraction")
    doc = next(d for d in body["documents"] if d["id"] == inv.id)
    assert doc["page_count"] == 2
    assert not doc.get("needs_reextraction")


def test_the_page_cache_follows_the_new_page_numbers(client, db_session):
    """The OCR cache is keyed by page number: left alone, page 2 would now read the removed
    page's text and the last page would be lost on the next extraction."""
    from app.api.v1.jobs import _job_doc_dir

    operator, job, inv, pl, bl = _setup(db_session)
    login(client, operator.email)
    client.delete(f"/api/v1/jobs/{job.id}/documents/{inv.id}/pages/2")

    ocr = _job_doc_dir(inv.id) / "ocr"
    assert sorted(f.name for f in ocr.glob("page_*.json")) == ["page_1.json", "page_2.json"]
    assert "CHARLIE" in json.loads((ocr / "page_2.json").read_text(encoding="utf-8"))["text"]
    db_session.expire_all()
    record = db_session.get(JobDocument, inv.id).extracted_json
    assert record["page_count"] == 2 and "BRAVO" not in record["text"] and "CHARLIE" in record["text"]


def test_a_highlight_on_a_later_page_moves_up_with_it(client, db_session):
    operator, job, inv, pl, bl = _setup(db_session)
    login(client, operator.email)
    client.delete(f"/api/v1/jobs/{job.id}/documents/{pl.id}/pages/1")

    db_session.expire_all()
    net = (db_session.query(JobFieldValue)
           .filter(JobFieldValue.job_id == job.id, JobFieldValue.label_name == "net_weight").one())
    assert net.extracted_value == "55" and net.found_page == 1
    v = _values(db_session, job)
    assert v["cartons"] == ""                      # it was on the packing list's removed page
    # the invoice's lines and their CTHs are not the packing list's to take
    assert [v[f"CTH#{i}"] for i in (1, 2, 3)] == ["85171000", "85172000", "85179999"]
    assert v["Invoice No"] == "INV-7781"


def test_removing_the_packing_list_keeps_a_field_that_also_reads_the_invoice(client, db_session):
    operator, job, inv, pl, bl = _setup(db_session)
    login(client, operator.email)

    resp = client.delete(f"/api/v1/jobs/{job.id}/documents/{pl.id}/file")
    assert resp.status_code == 200, resp.text
    v = _values(db_session, job)

    assert "cartons" not in v and "net_weight" not in v      # the packing list's own values
    assert v["Invoice No"] == "INV-7781"                     # reads invoice, PL and BL
    assert v["Customs House Code"] == "INBLR4"
    assert v["Care Note"] == "HANDLE WITH CARE"
    assert v["invoice_number"] == "INV-7781" and v["hbl"] == "HAWB-55"
    assert [v[f"CTH#{i}"] for i in (1, 2, 3)] == ["85171000", "85172000", "85179999"]


def test_removing_the_invoice_takes_its_lines_and_what_only_it_supplied(client, db_session):
    operator, job, inv, pl, bl = _setup(db_session)
    login(client, operator.email)

    client.delete(f"/api/v1/jobs/{job.id}/documents/{inv.id}/file")
    v = _values(db_session, job)

    assert not [k for k in v if k.startswith(("description#", "CTH#"))]
    assert v["Care Note"] == "" and v["Buyer Clean"] == ""   # read the invoice and nothing else
    assert v["Invoice No"] == "INV-7781"                     # the PL and the BL still say it
    assert v["Customs House Code"] == "INBLR4" and v["Bill of Entry Type"] == "P"
    assert v["cartons"] == "12" and v["hbl"] == "HAWB-55"


def test_a_page_that_was_never_ocrd_removes_nothing_and_asks_for_a_re_read(client, db_session):
    """Read-from-images jobs have no per-page text, so there is no telling what the page held.
    Nothing is removed on a guess - the document is marked for a re-read instead."""
    operator, job, inv, pl, bl = _setup(db_session, ocr=False)
    login(client, operator.email)
    before = _values(db_session, job)

    body = client.delete(f"/api/v1/jobs/{job.id}/documents/{inv.id}/pages/2").json()

    assert _values(db_session, job) == before
    doc = next(d for d in body["documents"] if d["id"] == inv.id)
    assert doc["needs_reextraction"] is True
