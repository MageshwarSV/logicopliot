"""app/core/doc_sets.py: pairing each invoice with its own packing list (and everything else
that carries a matching invoice number) by what's printed on the documents, not upload order.

No dedicated test file existed for this module before - added while root-causing a real bug:
a single-invoice job's own Invoice and Packing List got split into two FAKE sets because their
own "Invoice No"-captioned fields read two different strings, even though there was only ever
one file in each slot and so nothing to actually pair."""
from app.core.doc_sets import (
    assign_sets, describe_sets, extract_invoice_number_from_text, normalise_key, pairing_key,
    same_number,
)


def _file(fid, tid, file_index=0, key=None, name=None):
    return {"id": fid, "template_document_id": tid, "file_index": file_index, "key": key,
            "name": name or fid}


# ---- the bug this was found fixing --------------------------------------------------------

def test_one_file_per_slot_is_one_set_even_when_their_own_keys_disagree():
    """The exact live scenario: a single Invoice and a single Packing List, whose own
    'Invoice No' fields read two different strings. There is only one file in each slot, so
    there is nothing to pair - both must land in set 1, not be split into two fake sets."""
    files = [
        _file("inv1", "invoice-doc", key="ITI0626000011"),
        _file("pl1", "packing-list-doc", key="A54901"),
    ]
    assert assign_sets(files) == {"inv1": 1, "pl1": 1}


def test_one_file_per_slot_is_one_set_across_four_document_types_with_mixed_keys():
    files = [
        _file("bl1", "bl-doc", key=None),  # a Bill of Lading carries no invoice number
        _file("inv1", "invoice-doc", key="ITI0626000011"),
        _file("pl1", "packing-list-doc", key="A54901"),
        _file("fc1", "fright-cert-doc", key=None),
    ]
    assert assign_sets(files) == {"bl1": 1, "inv1": 1, "pl1": 1, "fc1": 1}


# ---- existing behavior this must not have disturbed ----------------------------------------

def test_no_keys_at_all_and_one_file_per_slot_is_still_one_set():
    files = [_file("inv1", "invoice-doc", key=None), _file("pl1", "packing-list-doc", key=None)]
    assert assign_sets(files) == {"inv1": 1, "pl1": 1}


def test_no_keys_at_all_and_multiple_invoices_still_get_distinct_sets():
    """Was `{"inv1": None, "inv2": None, "pl1": None}` - genuinely wrong: two SEPARATE invoice
    files, each uploaded on its own, collapsed onto the SAME set=None just because neither
    one's own invoice number could be read. Every per-row field (CTH, container type, ...) for
    one of them then silently lost its slot to the other, since both were "row 1 of set None".
    A file in the primary (invoice) slot is never "the whole job" the way a lone Bill of
    Lading can be, so it always gets its own set - packing-list-doc here (one file, not the
    primary slot) correctly stays unpaired, since there is genuinely no way to tell which
    invoice it belongs to."""
    files = [
        _file("inv1", "invoice-doc", 0, key=None),
        _file("inv2", "invoice-doc", 1, key=None),
        _file("pl1", "packing-list-doc", 0, key=None),
    ]
    sets = assign_sets(files)
    assert sorted(v for v in (sets["inv1"], sets["inv2"])) == [1, 2]
    assert sets["pl1"] is None


def test_an_unkeyed_invoice_among_keyed_ones_gets_its_own_set_not_none():
    # Same bug, partially keyed: the real job this was found on had 3 invoices uploaded, only
    # the first one's own invoice number read cleanly.
    files = [
        _file("inv1", "invoice-doc", 0, key="E26000505"),
        _file("inv2", "invoice-doc", 1, key=None),
        _file("inv3", "invoice-doc", 2, key=None),
        _file("pl1", "packing-list-doc", 0, key=None),
    ]
    sets = assign_sets(files)
    assert sets["inv1"] == 1
    assert sets["inv2"] is not None and sets["inv2"] != 1
    assert sets["inv3"] is not None and sets["inv3"] != 1
    assert sets["inv2"] != sets["inv3"]
    assert sets["pl1"] is None


def test_an_ambiguous_invoice_still_gets_its_own_set_not_none():
    # inv3's key is a short-form suffix of BOTH inv1's and inv2's own numbers - genuinely
    # ambiguous which it pairs with, but it is still its OWN invoice file and must not
    # collide with either of the other two.
    files = [
        _file("inv1", "invoice-doc", 0, key="E26000505"),
        _file("inv2", "invoice-doc", 1, key="F99000505"),
        _file("inv3", "invoice-doc", 2, key="505"),
    ]
    sets = assign_sets(files)
    assert sets["inv1"] == 1
    assert sets["inv2"] == 2
    assert sets["inv3"] not in (None, 1, 2)


def test_multiple_invoices_pair_with_their_own_packing_list_by_matching_number():
    files = [
        _file("inv1", "invoice-doc", 0, key="E26000505"),
        _file("inv2", "invoice-doc", 1, key="E26000507"),
        _file("pl1", "packing-list-doc", 0, key="E26000507"),
        _file("pl2", "packing-list-doc", 1, key="E26000505"),
    ]
    sets = assign_sets(files)
    assert sets["inv1"] == sets["pl2"]  # E26000505
    assert sets["inv2"] == sets["pl1"]  # E26000507
    assert sets["inv1"] != sets["inv2"]


def test_a_short_form_number_matches_its_full_form():
    # A packing list often prints "INV NO: 505" where the invoice says "E26000505".
    files = [
        _file("inv1", "invoice-doc", key="E26000505"),
        _file("pl1", "packing-list-doc", key="505"),
    ]
    sets = assign_sets(files)
    assert sets["inv1"] == sets["pl1"]


def test_a_distinctive_number_matching_no_invoice_still_gets_its_own_set():
    """An extra packing list with no invoice of its own is a real situation, not noise - it
    gets a new set number rather than being silently dropped (see assign_sets' own docstring)."""
    files = [
        _file("inv1", "invoice-doc", 0, key="E26000505"),
        _file("inv2", "invoice-doc", 1, key="E26000507"),
        _file("pl1", "packing-list-doc", 0, key="E26000507"),
        _file("pl2", "packing-list-doc", 1, key="ZZZ999"),  # matches neither invoice
    ]
    sets = assign_sets(files)
    assert sets["inv2"] == sets["pl1"]
    assert sets["pl2"] not in (None, sets["inv1"], sets["inv2"])


def test_an_ambiguous_key_matching_two_numbers_is_left_unpaired():
    # Two full invoice numbers that happen to share a "505" suffix - a packing list keyed on
    # just "505" fits either one, so it must be left unpaired rather than guessed.
    files = [
        _file("inv1", "invoice-doc", 0, key="E26000505"),
        _file("inv2", "invoice-doc", 1, key="F99000505"),
        _file("pl1", "packing-list-doc", key="505"),
    ]
    sets = assign_sets(files)
    assert sets["pl1"] is None


def test_a_whole_job_document_with_no_key_stays_unpaired_among_multiple_invoices():
    files = [
        _file("bl1", "bl-doc", key=None),
        _file("inv1", "invoice-doc", 0, key="E26000505"),
        _file("inv2", "invoice-doc", 1, key="E26000507"),
        _file("pl1", "packing-list-doc", 0, key="E26000507"),
        _file("pl2", "packing-list-doc", 1, key="E26000505"),
    ]
    sets = assign_sets(files)
    assert sets["bl1"] is None
    assert sets["inv1"] != sets["inv2"]


# ---- pairing_key / normalise_key -------------------------------------------------------------

def test_pairing_key_finds_the_best_evidence_label():
    assert pairing_key({"Commercial Invoice No": "E26000505", "PO Number": "PO123"}) == "E26000505"


def test_pairing_key_rejects_a_key_shorter_than_the_minimum():
    assert pairing_key({"Invoice No": "12"}) is None


def test_pairing_key_none_when_nothing_matches_an_invoice_label():
    assert pairing_key({"Container No": "MSCU1234567"}) is None


def test_normalise_key_strips_punctuation_and_case():
    assert normalise_key("E26/000505") == normalise_key("e26-000505") == "E26000505"


def test_describe_sets_reports_a_whole_job_document_separately():
    files = [_file("bl1", "bl-doc", key=None), _file("inv1", "invoice-doc", key="E26000505")]
    sets = {"bl1": None, "inv1": 1}
    lines = describe_sets(files, sets)
    assert any("whole job" in line for line in lines)
    assert any("set 1" in line for line in lines)


# ---- extract_invoice_number_from_text (Smart Upload's own grouping/splitting pass) ---------

def test_extract_invoice_number_reads_it_straight_off_raw_page_text():
    text = "COMMERCIAL INVOICE\nInvoice No: INV-0505\nSupplier: Acme Corp"
    assert extract_invoice_number_from_text("Invoice", text) == "INV0505"


def test_extract_invoice_number_works_for_packing_list_too():
    # A packing list names the invoice it belongs to, not a packing-list number of its own -
    # same pattern as Invoice, deliberately.
    text = "PACKING LIST\nInvoice No: INV-0505\n3 cartons"
    assert extract_invoice_number_from_text("PackingList", text) == "INV0505"


def test_extract_invoice_number_none_when_doc_type_not_groupable():
    text = "BILL OF LADING\nInvoice No: INV-0505"
    assert extract_invoice_number_from_text("BL", text) is None


def test_extract_invoice_number_none_when_nothing_matches():
    assert extract_invoice_number_from_text("Invoice", "no reference printed anywhere here") is None


def test_extract_invoice_number_none_for_too_short_a_number():
    assert extract_invoice_number_from_text("Invoice", "Invoice No: 12") is None


def test_same_number_matches_short_form_against_full_form():
    assert same_number(normalise_key("0505"), normalise_key("INV-000505"))


def test_same_number_rejects_unrelated_numbers():
    assert not same_number(normalise_key("INV-0505"), normalise_key("INV-0777"))
