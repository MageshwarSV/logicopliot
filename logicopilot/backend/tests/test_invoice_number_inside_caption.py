"""A boxed caption with the invoice number BETWEEN its words (Fabrinet, MAA/AI/34955/26-27):
"COMMERCIAL INVOICE" / "5200152" / "NUMBER". Six invoices, one per page, in one PDF."""
from app.core import doc_sets


def _page(number: str) -> str:
    return ("COMMERCIAL INVOICE & PACKING SLIP\nfabrinet\nCOMMERCIAL INVOICE\n"
            f"{number}\nNUMBER\nFABRINET CO., LTD\n"
            '"THIS IS A COMPUTER GENERATED INVOICE NO SIGNATURE IS REQUIRED"')


def test_value_inside_the_caption_is_read():
    assert doc_sets.extract_invoice_number_from_text("Invoice", _page("5200152")) == "5200152"


def test_one_invoice_per_page_is_split():
    nums = ["5200152", "5200460", "5200461", "5200567", "5201158", "5201492"]
    texts = {i + 1: _page(n) for i, n in enumerate(nums)}
    assert doc_sets.split_claim_pages_by_invoice_number("Invoice", texts) == [[1], [2], [3], [4], [5], [6]]


def test_existing_layouts_unchanged():
    assert doc_sets.extract_invoice_number_from_text("Invoice", "Invoice No: 8667395 Date") == "8667395"
    assert doc_sets.extract_invoice_number_from_text("Invoice", "GENERATED INVOICE NO SIGNATURE IS REQUIRED") is None
    assert doc_sets.extract_invoice_number_from_text("Invoice", "INVOICE\n23-11-2026\nNUMBER") is None
