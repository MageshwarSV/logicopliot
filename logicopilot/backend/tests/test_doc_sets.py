"""app/core/doc_sets.py: pairing each invoice with its own packing list (and everything else
that carries a matching invoice number) by what's printed on the documents, not upload order.

No dedicated test file existed for this module before - added while root-causing a real bug:
a single-invoice job's own Invoice and Packing List got split into two FAKE sets because their
own "Invoice No"-captioned fields read two different strings, even though there was only ever
one file in each slot and so nothing to actually pair."""
from app.core.doc_sets import assign_sets, describe_sets, normalise_key, pairing_key


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


def test_no_keys_at_all_and_multiple_files_in_a_slot_is_unpaired():
    files = [
        _file("inv1", "invoice-doc", 0, key=None),
        _file("inv2", "invoice-doc", 1, key=None),
        _file("pl1", "packing-list-doc", 0, key=None),
    ]
    assert assign_sets(files) == {"inv1": None, "inv2": None, "pl1": None}


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
