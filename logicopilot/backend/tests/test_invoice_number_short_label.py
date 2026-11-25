"""The invoice-number label written short: "Comm. Inv." (number under it) and "Inv No:" (number
to its right). JOB-A0D519: Vishay's 3-page "Commercial Invoice.pdf" is three invoices, one per
page, each numbered under "Comm. Inv. / Delivery Note" - and the only label the layout read saw
was the title "COMMERCIAL INVOICE", whose value underneath is the Purchase Order number."""
from app.core import doc_sets


def _t(text, x0, y0):
    return {"text": text, "x0": x0, "y0": y0, "x1": x0 + 0.006 * len(text), "y1": y0 + 0.012}


def _vishay(number: str, po: str) -> list[dict]:
    return [
        _t("COMMERCIAL", 0.195, 0.042), _t("INVOICE", 0.303, 0.044),
        _t("Comm", 0.188, 0.075), _t(".", 0.216, 0.075), _t("Inv", 0.222, 0.075), _t(".", 0.235, 0.075),
        _t("/", 0.240, 0.075), _t("Delivery", 0.247, 0.075), _t("Note", 0.284, 0.077),
        _t("Purchase", 0.320, 0.075), _t("Order", 0.370, 0.075),
        _t(number, 0.188, 0.095), _t(po, 0.320, 0.095),
    ]


def test_comm_inv_reads_the_invoice_number_not_the_po():
    assert doc_sets.invoice_number_from_tokens("Invoice", _vishay("6819894885", "868Q90094")) == "6819894885"


def test_glued_inv_token_is_read_too():
    toks = _vishay("6819888628", "868Q89668")
    toks[4:8] = [_t("Inv./Delivery", 0.2255, 0.075)]        # "Comm" "." "Inv./Delivery" "Note"
    assert [t["text"] for t in toks[2:6]] == ["Comm", ".", "Inv./Delivery", "Note"]
    assert doc_sets.invoice_number_from_tokens("Invoice", toks) == "6819888628"


def test_three_vishay_invoices_are_three_and_same_po_does_not_merge_them():
    pages = {1: _vishay("6819894885", "868Q90094"), 2: _vishay("6819888628", "868Q90094"),
             3: _vishay("6819872146", "868Q90094")}
    texts = {p: "" for p in pages}
    assert doc_sets.split_claim_pages_by_invoice_number("Invoice", texts, pages) == [[1], [2], [3]]


def test_same_invoice_number_on_two_pages_stays_one_invoice():
    pages = {1: _vishay("6819894885", "868Q90094"), 2: _vishay("6819894885", "868Q90094")}
    texts = {p: "" for p in pages}
    assert doc_sets.split_claim_pages_by_invoice_number("Invoice", texts, pages) == [[1, 2]]


def test_inline_inv_no_reads_right_not_the_address_below():
    toks = [_t("INV", 0.040, 0.134), _t("NO", 0.094, 0.134), _t(":", 0.131, 0.134),
            _t("IY", 0.160, 0.134), _t("26110509", 0.191, 0.134),
            _t("Plot", 0.040, 0.190), _t("no", 0.065, 0.190), _t("8,19,20", 0.104, 0.190)]
    assert doc_sets.invoice_number_from_tokens("PackingList", toks) == "26110509"
    # ...and that still pairs with the invoice's own "IY26110509"
    assert doc_sets.same_number("26110509", "IY26110509")


def test_inline_value_split_by_ocr_is_joined():
    toks = [_t("INV", 0.485, 0.170), _t("/", 0.503, 0.170), _t("NO", 0.509, 0.170), _t(".:", 0.521, 0.170),
            _t("AFY26", 0.533, 0.170), _t("-", 0.563, 0.170), _t("D395", 0.569, 0.170)]
    assert doc_sets.invoice_number_from_tokens("Invoice", toks) == "AFY26D395"


def test_full_invoice_no_label_still_wins():
    toks = [_t("Invoice", 0.10, 0.10), _t("No", 0.15, 0.10), _t("12345", 0.10, 0.12),
            _t("Inv", 0.50, 0.10), _t("No", 0.53, 0.10), _t("99999", 0.56, 0.10)]
    assert doc_sets.invoice_number_from_tokens("Invoice", toks) == "12345"
