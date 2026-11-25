"""An invoice labelled with the single word "Invoice" must still give up its number.

Production JOB-41EF33 (Sea Import, Allison Transmission). One file, 72G0085907.pdf, held a
7-page "COMMERCIAL INVOICE AND PACKING LIST". The per-page classifier judged every page on
its own - each page honestly looks like both an invoice and a packing list - so they came out
alternating, and the file became Invoice 1 (1 page), Invoice 2 (2 pages), Packing List 1
(1 page) and Packing List 2 (2 pages). One invoice, number 94286287, sitting in two slots.

Smart Upload ALREADY merges claims that share an invoice number into one document. It never
got the chance: the number read back None, so each claim stayed its own document.

Why None: Allison labels it with the bare word "Invoice" and prints the number beside it.
  - the flat-text patterns are line-based, and "Invoice" and "94286287" are separate lines
  - the layout rule required a TWO-word label ("Invoice No", "Delivery Number")

So the number is now also looked for beside a ONE-word label - last, only once every
two-word label has failed, so pages carrying "Invoice No" read exactly as they did.

The tests below use normalised token geometry, the same shape docai.ocr_page_image returns.
"""
from app.core.doc_sets import (
    invoice_number_from_tokens,
    same_number,
    split_claim_pages_by_invoice_number,
)


def _tok(text, x0, y0, w=0.06, h=0.012):
    return {"text": text, "x0": x0, "y0": y0, "x1": x0 + w, "y1": y0 + h}


def _allison_page(number: str) -> list[dict]:
    """Allison's header: a title band, then a little label/value table - the label a single
    word, its value in the next cell across."""
    return [
        _tok("COMMERCIAL", 0.05, 0.04), _tok("INVOICE", 0.14, 0.04),
        _tok("AND", 0.22, 0.04), _tok("PACKING", 0.27, 0.04), _tok("LIST", 0.35, 0.04),
        _tok("ALLISON", 0.05, 0.07), _tok("TRANSMISSION", 0.13, 0.07),
        _tok("Invoice", 0.60, 0.12), _tok(number, 0.72, 0.12),
        _tok("Customer", 0.60, 0.14), _tok("code", 0.68, 0.14), _tok("97962", 0.72, 0.14),
        _tok("Invoice", 0.60, 0.16), _tok("Date", 0.68, 0.16), _tok("10/21/2026", 0.72, 0.16),
    ]


def test_the_number_is_read_from_a_one_word_label():
    assert invoice_number_from_tokens("Invoice", _allison_page("94286287")) == "94286287"


def test_the_date_beside_invoice_date_is_never_taken_for_the_number():
    """"Invoice Date" sits in the same table and starts with the same word. Taking the value
    beside it would give every page of every supplier the same wrong key."""
    got = invoice_number_from_tokens("Invoice", _allison_page("94286287"))
    assert got == "94286287"
    assert "2026" not in (got or "")


def test_a_two_word_label_still_wins():
    """A page carrying "Invoice No" must read exactly as it did before - the bare-label pass
    only runs once every two-word label has failed."""
    page = [
        _tok("Invoice", 0.10, 0.10), _tok("No", 0.17, 0.10), _tok("E26000505", 0.24, 0.10),
        # A bare "Invoice" elsewhere on the page, with a number beside it that is NOT the
        # invoice number. The two-word label must answer first.
        _tok("Invoice", 0.10, 0.40), _tok("8888888", 0.20, 0.40),
    ]
    assert invoice_number_from_tokens("Invoice", page) == "E26000505"


def test_one_invoice_over_three_pages_is_not_split():
    """The job that started this: three pages, one number, one document."""
    pages = {n: "" for n in (1, 2, 3)}
    tokens = {n: _allison_page("94286287") for n in (1, 2, 3)}
    assert split_claim_pages_by_invoice_number("Invoice", pages, tokens) == [[1, 2, 3]]


def test_different_invoices_in_one_file_are_still_split_apart():
    """The case this must NOT break: JOB-A58BCB, a 40-page attachment holding separate
    invoices back to back. Reading MORE numbers must sharpen that split, never blunt it."""
    numbers = ["94286287", "94286287", "94290001", "94290001", "94291234"]
    pages = {n: "" for n in range(1, 6)}
    tokens = {n: _allison_page(num) for n, num in enumerate(numbers, start=1)}
    assert split_claim_pages_by_invoice_number("Invoice", pages, tokens) == [[1, 2], [3, 4], [5]]


def test_a_page_with_no_number_stays_with_the_invoice_before_it():
    """Unchanged behaviour, re-checked because the bare-label pass sees more pages than the
    two-word rule did: an unreadable page must not become a phantom extra invoice."""
    pages = {1: "", 2: "", 3: ""}
    tokens = {1: _allison_page("94286287"), 2: [_tok("continued", 0.1, 0.1)],
              3: _allison_page("94286287")}
    assert split_claim_pages_by_invoice_number("Invoice", pages, tokens) == [[1, 2, 3]]


def test_nothing_number_like_beside_the_label_reads_nothing():
    """It must keep returning None rather than inventing a key - a wrong key shared by every
    page would merge two genuinely different invoices into one document."""
    page = [_tok("Invoice", 0.60, 0.12), _tok("PREPAID", 0.72, 0.12)]
    assert invoice_number_from_tokens("Invoice", page) is None


def test_the_packing_list_side_reads_the_same_number():
    """Both halves of a combined document must agree, or they land in different sets."""
    page = _allison_page("94286287")
    assert invoice_number_from_tokens("PackingList", page) == "94286287"
    assert same_number(invoice_number_from_tokens("Invoice", page),
                       invoice_number_from_tokens("PackingList", page))
