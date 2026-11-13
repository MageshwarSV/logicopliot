"""Reading the invoice number off the page's LAYOUT rather than the flat word stream.

The flat OCR stream cannot tell a column header from the value under it. This supplier
(ARTESYN / Advanced Energy, live on JOB-1130B4 and JOB-CB9B46) prints a header band and a
value band:

    Invoice No:   Invoice Date:   Customer No.:   Currency:
    8667395       23-SEP-2026     130385798       USD

which Document AI streams as "... PH Invoice No: Invoice Date: Customer No.: Currency:
INCO2000 8667395 23-SEP-2026 130385798 USD". The old pattern captured the WORD "Invoice" -
the next column's header - as the invoice number, and did it identically on every page. Two
different invoices in one PDF therefore carried the same "number" and were never split apart.

The coordinates below are the real ones measured off that page, not invented.
"""
from app.core import doc_sets

# Measured from page 1 of "[20261023] Commercial Invoice 380.PDF" (normalised 0..1).
REAL_PAGE_1 = [
    {"text": "Invoice", "x0": 0.0236, "y0": 0.1671, "x1": 0.0588, "y1": 0.1765},
    {"text": "No", "x0": 0.0618, "y0": 0.1671, "x1": 0.0752, "y1": 0.1765},
    {"text": "Invoice", "x0": 0.1315, "y0": 0.1671, "x1": 0.1661, "y1": 0.1765},
    {"text": "Date", "x0": 0.1697, "y0": 0.1671, "x1": 0.1909, "y1": 0.1765},
    {"text": "Customer", "x0": 0.2418, "y0": 0.1663, "x1": 0.2879, "y1": 0.1765},
    {"text": "No", "x0": 0.2909, "y0": 0.1663, "x1": 0.3036, "y1": 0.1757},
    {"text": "Currency", "x0": 0.3533, "y0": 0.1678, "x1": 0.3982, "y1": 0.1788},
    {"text": "INCO2000", "x0": 0.4224, "y0": 0.1671, "x1": 0.4697, "y1": 0.1765},
    {"text": "INCO2000", "x0": 0.4230, "y0": 0.1796, "x1": 0.4764, "y1": 0.1945},
    {"text": "8667395", "x0": 0.0236, "y0": 0.1820, "x1": 0.0679, "y1": 0.1929},
]
# The same page of the SECOND invoice in that file - only the number differs.
REAL_PAGE_3 = [
    dict(t, text="8667396") if t["text"] == "8667395" else dict(t) for t in REAL_PAGE_1
]

# What the flat stream looks like for the same band - the text the old path had to work from.
FLAT_TEXT_1 = ("Kwun Tong, Kowloon Hong Kong Telephone: 21763333 Facsimile 21763845 "
               "PH Invoice No: Invoice Date: Customer No.: Currency: "
               "INCO2000 8667395 23-SEP-2026 130385798 USD INCO2000 FCA Laguna")
FLAT_TEXT_3 = FLAT_TEXT_1.replace("8667395", "8667396")


def test_the_number_is_read_from_under_its_own_label():
    assert doc_sets.invoice_number_from_tokens("Invoice", REAL_PAGE_1) == "8667395"
    assert doc_sets.invoice_number_from_tokens("Invoice", REAL_PAGE_3) == "8667396"


def test_the_neighbouring_column_does_not_answer_for_this_one():
    """INCO2000 sits on the same band, three columns to the right, and carries a digit. It is
    not this label's value and must never be returned as one."""
    assert doc_sets.invoice_number_from_tokens("Invoice", REAL_PAGE_1) != "INCO2000"


def test_the_flat_text_alone_no_longer_invents_a_number():
    """It cannot find 8667395 in that stream - but it must not answer "INVOICE" either. A
    wrong number repeated on every page is what merged two invoices into one."""
    got = doc_sets.extract_invoice_number_from_text("Invoice", FLAT_TEXT_1)
    assert got != "INVOICE", "the next column's header is not an invoice number"
    assert got is None or any(ch.isdigit() for ch in got)


def test_two_invoices_split_once_the_layout_is_available():
    """End to end over the splitter: the four pages of that file, text and tokens."""
    page_texts = {1: FLAT_TEXT_1, 2: "Terms and Conditions 1. AGREEMENT.",
                  3: FLAT_TEXT_3, 4: "Terms and Conditions 1. AGREEMENT."}
    page_tokens = {1: REAL_PAGE_1, 2: [], 3: REAL_PAGE_3, 4: []}
    groups = doc_sets.split_claim_pages_by_invoice_number("Invoice", page_texts, page_tokens)
    assert groups == [[1, 2], [3, 4]], (
        "each invoice takes its own page and the terms page that follows it"
    )


def test_without_tokens_it_falls_back_and_does_not_split_wrongly():
    """No geometry (OCR down, an image-only page): it must not split on a guess. One group is
    the old behaviour and is honest; two groups built on the word "INVOICE" would not be."""
    page_texts = {1: FLAT_TEXT_1, 2: "Terms and Conditions", 3: FLAT_TEXT_3, 4: "Terms"}
    groups = doc_sets.split_claim_pages_by_invoice_number("Invoice", page_texts)
    assert groups == [[1, 2, 3, 4]]


def test_a_plain_label_and_value_on_one_line_still_works():
    """The ordinary layout, which the flat pattern has always handled, must keep working.
    Punctuation is dropped on purpose - see normalise_key: 'E26/000505' and 'e26-000505' are
    the same invoice, so the key is the letters and digits alone."""
    assert doc_sets.extract_invoice_number_from_text(
        "Invoice", "COMMERCIAL INVOICE\nInvoice No: INV-0505\nShip to") == "INV0505"


def test_a_value_printed_to_the_right_of_its_label_is_read():
    tokens = [
        {"text": "Invoice", "x0": 0.10, "y0": 0.20, "x1": 0.16, "y1": 0.21},
        {"text": "No:", "x0": 0.17, "y0": 0.20, "x1": 0.20, "y1": 0.21},
        {"text": "E26000505", "x0": 0.22, "y0": 0.2005, "x1": 0.30, "y1": 0.21},
    ]
    assert doc_sets.invoice_number_from_tokens("Invoice", tokens) == "E26000505"


def test_a_date_under_the_label_is_not_mistaken_for_a_number():
    tokens = [
        {"text": "Invoice", "x0": 0.10, "y0": 0.20, "x1": 0.16, "y1": 0.21},
        {"text": "No", "x0": 0.17, "y0": 0.20, "x1": 0.20, "y1": 0.21},
        {"text": "23-SEP-2026", "x0": 0.10, "y0": 0.23, "x1": 0.20, "y1": 0.24},
    ]
    assert doc_sets.invoice_number_from_tokens("Invoice", tokens) is None


def test_no_tokens_and_a_non_groupable_type_are_both_safe():
    assert doc_sets.invoice_number_from_tokens("Invoice", None) is None
    assert doc_sets.invoice_number_from_tokens("Invoice", []) is None
    assert doc_sets.invoice_number_from_tokens("BL", REAL_PAGE_1) is None


def test_malformed_tokens_never_raise():
    """Tokens come off an external service; a missing coordinate must cost the layout read,
    not the classification."""
    assert doc_sets.invoice_number_from_tokens("Invoice", [{"text": "Invoice"}]) is None
    assert doc_sets.invoice_number_from_tokens(
        "Invoice", [{"text": "Invoice", "x0": 0.1}, {"text": "No"}]) is None


def test_looks_like_invoice_number_rules():
    assert doc_sets.looks_like_invoice_number("8667395")
    assert doc_sets.looks_like_invoice_number("E26000505")
    assert doc_sets.looks_like_invoice_number("INV-0505")
    assert not doc_sets.looks_like_invoice_number("INVOICE"), "no digit, not a number"
    assert not doc_sets.looks_like_invoice_number("Currency")
    assert not doc_sets.looks_like_invoice_number("23-SEP-2026"), "that is a date"
    assert not doc_sets.looks_like_invoice_number("23/09/2026")
    assert not doc_sets.looks_like_invoice_number("A1"), "too short to be distinctive"


# --------------------------------------------------------------------------------------
# A packing list is often numbered by its DELIVERY, never by an invoice. Two of them in one
# PDF are still two documents and must be split apart - live on JOB-1130B4, where
# "[20261023] Packing List 271.PDF" held delivery 8667395 and delivery 8667396 and landed as
# one 2-page document because neither page prints the words "Invoice No" anywhere.
# --------------------------------------------------------------------------------------

def _delivery_tokens(number: str) -> list[dict]:
    """"Delivery Number" with its value directly underneath, as the real page prints it."""
    return [
        {"text": "Delivery", "x0": 0.770, "y0": 0.120, "x1": 0.840, "y1": 0.131},
        {"text": "Number", "x0": 0.845, "y0": 0.120, "x1": 0.910, "y1": 0.131},
        {"text": number, "x0": 0.770, "y0": 0.140, "x1": 0.830, "y1": 0.151},
        {"text": "PACKING", "x0": 0.60, "y0": 0.05, "x1": 0.70, "y1": 0.07},
        {"text": "LIST", "x0": 0.71, "y0": 0.05, "x1": 0.76, "y1": 0.07},
    ]


def test_a_packing_list_is_numbered_by_its_delivery_number():
    assert doc_sets.invoice_number_from_tokens(
        "PackingList", _delivery_tokens("8667395")) == "8667395"


def test_an_invoice_is_never_numbered_by_a_delivery_number():
    """Only a packing list falls back to the delivery number. An Invoice slot keeps meaning
    the invoice number and nothing else."""
    assert doc_sets.invoice_number_from_tokens(
        "Invoice", _delivery_tokens("8667395")) is None


def test_the_invoice_number_still_wins_when_a_packing_list_prints_both():
    tokens = _delivery_tokens("9999999") + [
        {"text": "Invoice", "x0": 0.10, "y0": 0.30, "x1": 0.16, "y1": 0.31},
        {"text": "No", "x0": 0.17, "y0": 0.30, "x1": 0.20, "y1": 0.31},
        {"text": "8667395", "x0": 0.10, "y0": 0.33, "x1": 0.18, "y1": 0.34},
    ]
    assert doc_sets.invoice_number_from_tokens("PackingList", tokens) == "8667395"


def test_two_packing_lists_in_one_file_split_apart():
    page_texts = {1: "PACKING LIST Delivery Number 8667395 Waybill No. TBA",
                  2: "PACKING LIST Delivery Number 8667396 Waybill No. TBA"}
    groups = doc_sets.split_claim_pages_by_invoice_number("PackingList", page_texts)
    assert groups == [[1], [2]], "two deliveries are two packing lists"


def test_the_delivery_fallback_reads_off_flat_text_too():
    assert doc_sets.extract_invoice_number_from_text(
        "PackingList", "PACKING LIST Delivery Number: 8667396 Date Shipped") == "8667396"
    assert doc_sets.extract_invoice_number_from_text(
        "Invoice", "PACKING LIST Delivery Number: 8667396") is None
