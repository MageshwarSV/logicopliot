"""A packing list whose invoice number would not read must still join its own invoice's set.

Sets are paired by invoice NUMBER. A packing list that prints no readable number gets no key
and lands on set=None - unpaired - even when it plainly belongs to one invoice. Its fields
then go nowhere, and the operator sees "the second invoice shows fewer fields than the first".

Live, BLR/AI/3993/26-27 (production, client complaint). ONE PDF, 22H0524149.pdf, split into
three invoices:

    fidx=0  Invoice set=1  5001457558   Packing List set=1
    fidx=1  Invoice set=2  5001457557   Packing List set=None   <- 4 pages, no number read
    fidx=2  Invoice set=3  5001457556   Packing List set=3

Filled job-level fields came out 18 / 9 / 17. The nine missing from set 2 were exactly the
packing list's: Net Weight, Net Weight Unit, package_count.

Measured over 14 days of production: 31 orphaned packing lists across 20 jobs.

The danger this must not create is the opposite one. An Air Waybill, a Bill of Lading or a
Freight Certificate covers the WHOLE job; pulling one into set 1 would stop its values
applying to the other invoices. Over the same 14 days, a rule without that restriction would
have wrongly pulled in 28 of them - so the GROUPABLE_DOC_TYPES check is tested here as
carefully as the fix itself.
"""
from app.core.doc_sets import assign_sets

INV = "Invoice"          # template_document_id for the invoice slot
PL = "PackingList"       # ... and the packing list slot
AWB = "AirWaybill"       # a WHOLE-JOB slot - never per invoice


def _f(fid, slot, doc_type, file_index, key, name="22H0524149.pdf"):
    return {"id": fid, "template_document_id": slot, "doc_type": doc_type,
            "file_index": file_index, "name": name, "key": key}


def test_the_production_job_pairs_its_orphaned_packing_list():
    """BLR/AI/3993 exactly: three invoices, the middle packing list unreadable."""
    files = [
        _f("i1", INV, "Invoice", 0, "5001457558"),
        _f("p1", PL, "PackingList", 0, "5001457558"),
        _f("i2", INV, "Invoice", 1, "5001457557"),
        _f("p2", PL, "PackingList", 1, None),          # <- the one that read nothing
        _f("i3", INV, "Invoice", 2, "5001457556"),
        _f("p3", PL, "PackingList", 2, "5001457556"),
    ]
    out = assign_sets(files)
    assert out["i2"] == 2
    assert out["p2"] == out["i2"], (
        f"the packing list cut out beside invoice 2 must join set 2 - got {out['p2']}")
    # And nothing else moved.
    assert (out["i1"], out["p1"]) == (1, 1)
    assert (out["i3"], out["p3"]) == (3, 3)


def test_a_whole_job_document_is_never_pulled_into_one_set():
    """THE regression this must not cause. One Air Waybill covers all three invoices; forcing
    it into a set would stop its values reaching the other two."""
    files = [
        _f("i1", INV, "Invoice", 0, "AAA111"),
        _f("i2", INV, "Invoice", 1, "BBB222"),
        _f("awb", AWB, "AirWaybill", 0, None),     # same file, same file_index as invoice 1
    ]
    out = assign_sets(files)
    assert out["awb"] is None, "a whole-job document stays unpaired"


def test_a_freight_certificate_is_left_alone_too():
    files = [
        _f("i1", INV, "Invoice", 0, "AAA111"),
        _f("i2", INV, "Invoice", 1, "BBB222"),
        _f("fc", "Freight", "Freight", 1, None),
    ]
    assert assign_sets(files)["fc"] is None


def test_a_packing_list_from_a_DIFFERENT_file_is_not_paired():
    """file_index only means "the Nth claim in this slot". Two separate attachments can both
    be #1 without being related - JOB-7C8DA9, where the invoice at index 1 was a Pro Forma
    from one attachment and the packing list at index 1 came from another."""
    files = [
        _f("i1", INV, "Invoice", 0, "AAA111", name="invoice.pdf"),
        _f("i2", INV, "Invoice", 1, "BBB222", name="proforma.pdf"),
        _f("p?", PL, "PackingList", 1, None, name="email-copy.pdf"),
    ]
    assert assign_sets(files)["p?"] is None


def test_an_ambiguous_orphan_is_left_unpaired():
    """If the mates point at more than one set, nothing is decided - saying "this needs a
    look" beats guessing which invoice a document belongs to."""
    files = [
        _f("i1", INV, "Invoice", 0, "AAA111"),
        _f("i2", INV, "Invoice", 0, "BBB222"),   # same file AND same index as i1
        _f("p?", PL, "PackingList", 0, None),
    ]
    out = assign_sets(files)
    assert out["p?"] in (None, out["i1"]) and not (out["p?"] == out["i2"] != out["i1"]), \
        "an ambiguous orphan must not be silently attached to one of two candidates"


def test_a_packing_list_that_DID_read_its_number_is_unaffected():
    """The ordinary path must not change: a keyed packing list pairs by number, even when the
    file_index would have suggested a different invoice."""
    files = [
        _f("i1", INV, "Invoice", 0, "AAA111"),
        _f("i2", INV, "Invoice", 1, "BBB222"),
        _f("p1", PL, "PackingList", 1, "AAA111"),   # index says 2, number says invoice 1
    ]
    out = assign_sets(files)
    assert out["p1"] == out["i1"], "the invoice NUMBER still wins over the split position"


def test_a_single_invoice_job_is_untouched():
    files = [_f("i1", INV, "Invoice", 0, "AAA111"), _f("p1", PL, "PackingList", 0, None)]
    out = assign_sets(files)
    assert out == {"i1": 1, "p1": 1}


def test_a_document_with_no_source_name_is_not_paired():
    """`name` falls back to a per-slot label ("Invoice #2" vs "Packing List #2"), which can
    never match - the rule fails closed rather than pairing on file_index alone."""
    files = [
        _f("i1", INV, "Invoice", 0, "AAA111", name="Invoice #1"),
        _f("i2", INV, "Invoice", 1, "BBB222", name="Invoice #2"),
        _f("p?", PL, "PackingList", 1, None, name="Packing List #2"),
    ]
    assert assign_sets(files)["p?"] is None


def test_an_unkeyed_invoice_still_gets_its_own_set_and_can_be_joined():
    """The orphan pass runs AFTER the primary-slot renumber, so a packing list can join an
    invoice that itself had no readable number."""
    files = [
        _f("i1", INV, "Invoice", 0, "AAA111"),
        _f("i2", INV, "Invoice", 1, None),       # no number of its own
        _f("p2", PL, "PackingList", 1, None),
    ]
    out = assign_sets(files)
    assert out["i2"] is not None, "an unkeyed invoice still gets a set of its own"
    assert out["p2"] == out["i2"]
