"""The same document arriving twice, in two different files.

A supplier sends "Invoice.pdf" holding the invoice AND the packing list, then sends
"Packing List.pdf" holding the same two. The classifier is right about both: each file
really does contain both types. But the result was the Invoice slot holding two invoices
and the Packing List slot holding two packing lists - four rows, two actual documents
(JOB-3DB0E9).

Comparing the files on disk does not catch it. Each slot's pages are carved out of their own
source, so the two Invoice PDFs came to 26,816 bytes EACH with different sha256s: identical
content, different PDF timestamps. What is genuinely the same is the text.

The line this has to walk: drop the second copy of the same document, while never merging
three real invoices for one shipment into one.
"""
from app.core.page_filter import claim_fingerprint


def _file_text(*pages: str) -> str:
    """The flattened per-page text both Smart Upload and the email puller build."""
    return "\n\n".join(f"=== PAGE {n} ===\n{t}" for n, t in enumerate(pages, start=1))


INVOICE_PAGE = (
    "COMMERCIAL INVOICE\n"
    "Invoice No: E26000505   Date: 14/10/2026\n"
    "Seller: SEG Automotive Germany GmbH\n"
    "Buyer: 4S Logistics Pvt Ltd\n"
    "NJ-3001-60FB LIQUID SILICONE RUBBER   120 PCS   USD 4,380.00\n"
    "Total: USD 4,380.00\n"
)
PACKING_PAGE = (
    "PACKING LIST\n"
    "Invoice No: E26000505   Date: 14/10/2026\n"
    "Marks and Numbers: 1-6\n"
    "NJ-3001-60FB LIQUID SILICONE RUBBER   120 PCS\n"
    "Net Weight 240.00 KGS   Gross Weight 268.00 KGS\n"
)


def test_the_same_page_in_two_different_files_fingerprints_the_same():
    """The actual bug. Both attachments carry the invoice; it is one document."""
    first = _file_text(INVOICE_PAGE, PACKING_PAGE)       # "Invoice.pdf"
    second = _file_text(PACKING_PAGE, INVOICE_PAGE)      # "Packing List.pdf", other order

    # Each file's own claim on the Invoice slot points at a different page NUMBER...
    assert claim_fingerprint(first, [1]) == claim_fingerprint(second, [2])
    # ...and so does each file's claim on the Packing List slot.
    assert claim_fingerprint(first, [2]) == claim_fingerprint(second, [1])


def test_two_genuinely_different_invoices_never_collide():
    """The thing that must not break: three invoices for one shipment stay three rows."""
    a = _file_text(INVOICE_PAGE)
    b = _file_text(INVOICE_PAGE.replace("E26000505", "E26000506").replace("4,380.00", "9,120.00"))
    assert claim_fingerprint(a, [1]) != claim_fingerprint(b, [1])


def test_an_invoice_and_a_packing_list_are_not_the_same_document():
    """They quote each other's numbers and list the same goods, so a careless comparison
    would call them identical - and then one slot would silently lose its document."""
    text = _file_text(INVOICE_PAGE, PACKING_PAGE)
    assert claim_fingerprint(text, [1]) != claim_fingerprint(text, [2])


def test_whitespace_and_case_differences_do_not_make_a_new_document():
    """The same page OCR'd out of two files differs in line breaks and spacing - that is
    exactly the noise this has to see through."""
    a = _file_text(INVOICE_PAGE)
    noisy = INVOICE_PAGE.upper().replace("\n", "\n\n").replace("   ", "\t  ")
    b = _file_text(noisy)
    assert claim_fingerprint(a, [1]) == claim_fingerprint(b, [1])


def test_a_multi_page_claim_is_fingerprinted_over_all_its_pages():
    """A two-page invoice is one document, and its second page is part of what identifies
    it - otherwise two invoices sharing a first page would be judged the same."""
    page2 = "Continued\nBank details: HSBC 123456\nSignature\n" + "x" * 60
    a = _file_text(INVOICE_PAGE, page2)
    b = _file_text(INVOICE_PAGE, page2.replace("123456", "999999"))
    assert claim_fingerprint(a, [1]) == claim_fingerprint(b, [1])          # page 1 alone
    assert claim_fingerprint(a, [1, 2]) != claim_fingerprint(b, [1, 2])    # both pages


def test_page_order_within_one_claim_does_not_matter():
    """Pages are collected in the file's own order, so asking for [2, 1] gives the same
    document as [1, 2] rather than a second, different-looking one."""
    text = _file_text(INVOICE_PAGE, "Continued " + "y" * 60)
    assert claim_fingerprint(text, [1, 2]) == claim_fingerprint(text, [2, 1])


def test_an_unreadable_page_is_never_a_duplicate_of_anything():
    """A scan the OCR could not read gives almost no text. Two of those are not the same
    document - they are two documents nobody can read, and merging them would lose one."""
    blank = _file_text("", "")
    almost_blank = _file_text("page 1 of 2")
    assert claim_fingerprint(blank, [1]) == ""
    assert claim_fingerprint(almost_blank, [1]) == ""


def test_no_pages_and_no_text_are_handled():
    assert claim_fingerprint("", [1]) == ""
    assert claim_fingerprint(_file_text(INVOICE_PAGE), []) == ""


def test_a_page_number_that_does_not_exist_is_ignored_not_crashed():
    """A claim can name a page outside the kept range when filtering dropped one."""
    text = _file_text(INVOICE_PAGE)
    assert claim_fingerprint(text, [99]) == ""
    # ...and a mix still fingerprints on the page that does exist.
    assert claim_fingerprint(text, [1, 99]) == claim_fingerprint(text, [1])
