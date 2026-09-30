"""assign_documents_detailed: the deterministic reconciliation that runs AFTER the model
classifies each file - same-page collisions dropped, slots contested by specificity.
classify_document (the actual OpenAI call) is mocked out per file so these tests exercise
only the reconciliation logic, not the model."""

from unittest.mock import MagicMock, patch

from app.core.classifier import _keyword_signature_match, assign_documents, assign_documents_detailed, classify_document


def _file(name, text="", page_count=1):
    return {"name": name, "text": text, "image": None, "page_count": page_count}


def _claim(key, pages, evidence="some evidence text"):
    return {"key": key, "pages": pages, "evidence": evidence}


CANDIDATES = [
    {"key": "inv", "name": "Invoice", "doc_type": "Invoice", "fields": []},
    {"key": "pl", "name": "Packing List", "doc_type": "PackingList", "fields": []},
    {"key": "bl", "name": "BL", "doc_type": "BL", "fields": []},
    {"key": "frt", "name": "Freight", "doc_type": "Freight", "fields": []},
]


def _run(files, per_file_claims):
    with patch("app.core.classifier.classify_document", side_effect=per_file_claims):
        return assign_documents_detailed(files, CANDIDATES)


# --------------------------------------------------------------------------- #
# Positive cases
# --------------------------------------------------------------------------- #

def test_single_file_single_type_passes_through():
    files = [_file("a.pdf")]
    result = _run(files, [[_claim("inv", [1])]])
    assert [m["key"] for m in result[0]] == ["inv"]


def test_one_pdf_with_three_invoice_instances_all_kept():
    # The "Bug 1" fix: three physically distinct invoices in one PDF, same key, disjoint
    # pages - all three must survive, not collapse into one.
    files = [_file("combined.pdf")]
    claims = [[_claim("inv", [1]), _claim("inv", [2]), _claim("inv", [3])]]
    result = _run(files, claims)
    assert [m["key"] for m in result[0]] == ["inv", "inv", "inv"]


def test_three_files_each_one_invoice_all_win_the_slot():
    # Three separate invoice files, each an equally specific (breadth=1) claimant - all three
    # must be kept in the Invoice slot, not just the first.
    files = [_file("inv1.pdf"), _file("inv2.pdf"), _file("inv3.pdf")]
    claims = [[_claim("inv", [1])], [_claim("inv", [1])], [_claim("inv", [1])]]
    result = _run(files, claims)
    assert all(m["key"] == "inv" for ms in result for m in ms)
    assert sum(len(ms) for ms in result) == 3


def test_specific_claimant_beats_broad_claimant_for_a_contested_slot():
    # File 0 claims ONLY Freight (specific, breadth=1). File 1 claims BL AND Freight
    # (broad, breadth=2). File 0 must win Freight; file 1 keeps BL but loses Freight.
    files = [_file("freight_cert.pdf"), _file("bl_and_freight.pdf")]
    claims = [
        [_claim("frt", [1])],
        [_claim("bl", [1]), _claim("frt", [2])],
    ]
    result = _run(files, claims)
    assert [m["key"] for m in result[0]] == ["frt"]
    assert [m["key"] for m in result[1]] == ["bl"]


def test_combined_bl_plus_pl_on_different_pages_keeps_both():
    files = [_file("bl_pl_combo.pdf")]
    claims = [[_claim("bl", [1]), _claim("pl", [2])]]
    result = _run(files, claims)
    assert {m["key"] for m in result[0]} == {"bl", "pl"}


def test_multiple_winners_of_one_slot_are_all_reported():
    # Two files both claim the specificity-winning bar for the same slot - both kept, not
    # arbitrarily reduced to one.
    files = [_file("inv_a.pdf"), _file("inv_b.pdf")]
    claims = [[_claim("inv", [1])], [_claim("inv", [1])]]
    result = _run(files, claims)
    assert len(result[0]) == 1 and len(result[1]) == 1


# --------------------------------------------------------------------------- #
# Negative / edge cases
# --------------------------------------------------------------------------- #

def test_same_page_different_types_are_both_kept_for_a_combined_document():
    # A "SHIPPING INVOICE CUM PACKING LIST" - one real page that genuinely is both an
    # Invoice and a Packing List at once. Different keys sharing a page is NOT a conflict;
    # the same file must be written into both slots.
    files = [_file("shipping_invoice_cum_pl.pdf")]
    claims = [[
        _claim("inv", [1], evidence="short"),
        _claim("pl", [1], evidence="a much longer and more convincing quote of the page text"),
    ]]
    result = _run(files, claims)
    assert {m["key"] for m in result[0]} == {"inv", "pl"}
    assert len(result[0]) == 2


def test_same_key_claiming_an_already_claimed_page_is_still_dropped():
    # The SAME type claiming a page twice IS still a real conflict (unlike different types
    # sharing a page, above) - the one with the longer evidence wins.
    files = [_file("weird.pdf")]
    claims = [[
        _claim("inv", [1], evidence="short"),
        _claim("inv", [1], evidence="a much longer and more convincing quote of the page text"),
    ]]
    result = _run(files, claims)
    assert len(result[0]) == 1
    assert result[0][0]["evidence"] == "a much longer and more convincing quote of the page text"


def test_no_files_returns_empty_list():
    assert _run([], []) == []


def test_files_with_no_matches_at_all_return_empty_per_file():
    files = [_file("junk.pdf"), _file("more_junk.pdf")]
    result = _run(files, [[], []])
    assert result == [[], []]


def test_assign_documents_wrapper_returns_bare_keys_only():
    files = [_file("a.pdf")]
    with patch("app.core.classifier.classify_document", side_effect=[[_claim("inv", [1])]]):
        result = assign_documents(files, CANDIDATES)
    assert result == [["inv"]]


def test_non_overlapping_pages_for_same_key_are_never_treated_as_a_clash():
    # Two instances of the SAME key on a single file must not collide with each other just
    # because they share a key - only an actual page overlap should ever drop one.
    files = [_file("two_invoices.pdf")]
    claims = [[_claim("inv", [1, 2]), _claim("inv", [3, 4])]]
    result = _run(files, claims)
    assert len(result[0]) == 2


# --------------------------------------------------------------------------- #
# Keyword-signature backstop for a genuinely combined document - the model's own
# call sometimes notices only ONE of two types truly present on the same page.
# --------------------------------------------------------------------------- #

# Real structure from an actual "SHIPPING INVOICE CUM PACKING LIST" document.
HUSKY_STYLE_TEXT = """
SHIPPING INVOICE CUM PACKING LIST
Exporter: Husky Injection Molding Systems (India) Private Limited
INVOICE NO: EX/26-27/0413   INVOICE DATE: 3-Aug-2026
Description of Goods   HSN Ref.   Quantity   Rate in USD   Amount in USD
MANIFOLD SYSTEM 4 DROP 750-HT-PR-MS   84779000   1   2640.20   2,640.20
Total No. of Packs: 1-WOODEN BOX
Net Weight of Total Packs: 17.000 KGS
Gross Weight of Total Packs: 22.000 KGS
Dimension of each Pack: 620X420X380-1 IN MM
Terms of Payment: Net 60 days
"""

ORDINARY_INVOICE_TEXT = """
COMMERCIAL INVOICE
INVOICE NO: INV-9821   INVOICE DATE: 12-Jul-2026
Description of Goods   HSN   Quantity   Rate in USD   Amount in USD
Widget assembly   84779000   10   50.00   500.00
Net Weight: 5.5 KGS
Terms of Payment: Net 30 days
"""

ORDINARY_PACKING_LIST_TEXT = """
PACKING LIST
Net Weight: 22.000 KGS   Gross Weight: 24.500 KGS
No. of Packages: 2   Dimension: 400X300X200 MM
Marks & Nos.: ABC-001
"""

# The real production document that exposed the gap: neither "packing list" nor "invoice no"
# appears anywhere on the page. It calls itself "Commercial Invoice / Packing Slip", labels its
# invoice number field "Commercial Inv No", and its packing table "PACKING DETAILS".
SANSERA_STYLE_TEXT = """
SANSERA
Delivery Challan cum
Commercial Invoice / Packing Slip
Commercial Inv No / Date:
4914437463/ 27.08.2026
Total value
SI No Part No Description HSN Qty Unit Rate/Unit
1 715-173869-200 84869000 9,00 EA 49.60
Amount (In Words) USD: SEVEN THOUSAND SIX HUNDRED FIFTY-FOUR
PACKING DETAILS
Sl no Pkg Part no Type Qty Gross weight Net weight No of Box Box Dimensions
1 Box-1 715-173869-200 Carton Box 3 10 8,1 1 600 X 600 X 300 MM
"""


def test_keyword_backstop_adds_packinglist_when_model_only_found_invoice():
    files = [_file("combined.pdf", text=HUSKY_STYLE_TEXT)]
    claims = [[_claim("inv", [1], evidence="invoice fields present")]]
    result = _run(files, claims)
    assert {m["key"] for m in result[0]} == {"inv", "pl"}
    pl_claim = next(m for m in result[0] if m["key"] == "pl")
    assert pl_claim["pages"] == [1]


def test_keyword_backstop_adds_invoice_when_model_only_found_packinglist():
    files = [_file("combined.pdf", text=HUSKY_STYLE_TEXT)]
    claims = [[_claim("pl", [1], evidence="packing list fields present")]]
    result = _run(files, claims)
    assert {m["key"] for m in result[0]} == {"inv", "pl"}


def test_keyword_backstop_adds_invoice_on_a_real_combined_challan_the_model_only_saw_as_pl():
    # The exact production case that exposed the gap: SANSERA's "Delivery Challan cum
    # Commercial Invoice / Packing Slip" - the model only recognised the packing side (PL),
    # and neither "invoice no" nor "packing list" appears anywhere on the page, so the
    # required-phrase list had to widen to the real wording before this could pass.
    files = [_file("sansera_challan.pdf", text=SANSERA_STYLE_TEXT)]
    claims = [[_claim("pl", [1], evidence="packing details present")]]
    result = _run(files, claims)
    assert {m["key"] for m in result[0]} == {"inv", "pl"}


def _only_packinglist_verified(filename, text, image_path, candidate, already_types):
    # CANDIDATES now also has a Freight slot ("frt"), so a file matching neither PackingList
    # nor Freight by regex triggers an AI check for BOTH remaining types - a real fixture must
    # distinguish which one it is verifying, not return the same verdict for either.
    if candidate["doc_type"] == "PackingList":
        return {"pages": [1], "evidence": "a per-package weight breakdown"}
    return None


def test_ai_fallback_adds_the_second_type_when_regex_does_not_recognise_the_wording():
    # A company whose wording matches NEITHER regex pattern at all (a "Despatch Note" that is
    # also the commercial paperwork) - the AI fallback is the only thing that can catch this.
    files = [_file("despatch.pdf", text="DESPATCH NOTE\nSome wording regex has never seen.")]
    claims = [[_claim("inv", [1], evidence="invoice fields present")]]
    with patch("app.core.classifier._ai_verify_second_type", side_effect=_only_packinglist_verified):
        result = _run(files, claims)
    assert {m["key"] for m in result[0]} == {"inv", "pl"}
    added = next(m for m in result[0] if m["key"] == "pl")
    assert "AI-verified" in added["evidence"]


def test_ai_fallback_is_not_called_when_regex_already_matched():
    from unittest.mock import Mock

    files = [_file("combined.pdf", text=HUSKY_STYLE_TEXT)]
    claims = [[_claim("inv", [1], evidence="invoice fields present")]]
    mock_ai = Mock(return_value=None)
    with patch("app.core.classifier._ai_verify_second_type", mock_ai):
        result = _run(files, claims)
    assert {m["key"] for m in result[0]} == {"inv", "pl"}
    # PackingList was already caught by regex, so the AI fallback is never consulted for it -
    # it may still be called for the OTHER remaining type (Freight), which regex also rejects.
    for call in mock_ai.call_args_list:
        assert call.args[3]["doc_type"] != "PackingList"


def test_ai_fallback_returning_not_present_adds_nothing():
    files = [_file("plain_invoice.pdf", text=ORDINARY_INVOICE_TEXT)]
    claims = [[_claim("inv", [1], evidence="invoice fields present")]]
    with patch("app.core.classifier._ai_verify_second_type", return_value=None):
        result = _run(files, claims)
    assert [m["key"] for m in result[0]] == ["inv"]


def test_ai_fallback_is_not_called_when_every_slot_is_already_claimed():
    from unittest.mock import Mock

    files = [_file("combined.pdf", text=HUSKY_STYLE_TEXT)]
    claims = [[
        _claim("inv", [1], evidence="invoice fields present"),
        _claim("pl", [1], evidence="packing list fields present"),
        # "bl" has no signature at all (never checked either way) - "frt" DOES have one, so
        # it must be claimed too for "every slot" to actually be true among covered types.
        _claim("frt", [1], evidence="freight fields present"),
    ]]
    mock_ai = Mock()
    with patch("app.core.classifier._ai_verify_second_type", mock_ai):
        _run(files, claims)
    mock_ai.assert_not_called()


def test_ai_fallback_without_an_api_key_returns_none_without_crashing():
    from app.core.classifier import _ai_verify_second_type

    candidate = {"key": "pl", "name": "Packing List", "doc_type": "PackingList"}
    result = _ai_verify_second_type("f.pdf", "some text", None, candidate, ["Invoice"])
    assert result is None


def test_keyword_backstop_does_not_fire_on_an_ordinary_invoice_with_one_weight_field():
    # A real, single-type invoice that happens to mention "Net Weight" once must NOT get a
    # phantom Packing List added - it never says "packing list" and has none of the other
    # supporting markers (no gross weight, no dimensions, no marks & nos).
    files = [_file("plain_invoice.pdf", text=ORDINARY_INVOICE_TEXT)]
    claims = [[_claim("inv", [1], evidence="invoice fields present")]]
    result = _run(files, claims)
    assert [m["key"] for m in result[0]] == ["inv"]


def test_keyword_backstop_does_not_fire_on_an_ordinary_packing_list():
    files = [_file("plain_pl.pdf", text=ORDINARY_PACKING_LIST_TEXT)]
    claims = [[_claim("pl", [1], evidence="packing list fields present")]]
    result = _run(files, claims)
    assert [m["key"] for m in result[0]] == ["pl"]


def test_keyword_backstop_does_nothing_when_no_slot_exists_for_the_missing_type():
    candidates_no_pl = [{"key": "inv", "name": "Invoice", "doc_type": "Invoice", "fields": []}]
    files = [_file("combined.pdf", text=HUSKY_STYLE_TEXT)]
    with patch("app.core.classifier.classify_document",
               side_effect=[[_claim("inv", [1], evidence="invoice fields present")]]):
        result = assign_documents_detailed(files, candidates_no_pl)
    assert [m["key"] for m in result[0]] == ["inv"]


def test_keyword_backstop_does_not_duplicate_when_model_already_found_both():
    files = [_file("combined.pdf", text=HUSKY_STYLE_TEXT)]
    claims = [[
        _claim("inv", [1], evidence="invoice fields present"),
        _claim("pl", [1], evidence="packing list fields present"),
    ]]
    result = _run(files, claims)
    assert len(result[0]) == 2


def test_keyword_backstop_handles_empty_text_without_crashing():
    files = [_file("blank.pdf", text="")]
    claims = [[_claim("inv", [1])]]
    result = _run(files, claims)
    assert [m["key"] for m in result[0]] == ["inv"]


def test_keyword_backstop_multipage_claim_extends_augmentation_to_all_matched_pages():
    # The existing claim spans two pages - the added claim for the missing type must cover
    # the SAME pages, not just the first one.
    files = [_file("combined2.pdf", text=HUSKY_STYLE_TEXT)]
    claims = [[_claim("inv", [1, 2], evidence="invoice fields present")]]
    result = _run(files, claims)
    pl_claim = next(m for m in result[0] if m["key"] == "pl")
    assert pl_claim["pages"] == [1, 2]


def test_keyword_backstop_only_augments_the_relevant_file_in_a_batch():
    # Three files in one batch, none contesting the same slot as another (kept deliberately
    # non-competing - a SEPARATE test below covers what happens when two files DO compete for
    # the same slot): only the combined one should gain a second claim; a plain single-type
    # invoice for a DIFFERENT shipment and an unrelated BL file must be left exactly as the
    # model found them.
    files = [
        _file("combined.pdf", text=HUSKY_STYLE_TEXT),
        _file("plain_freight.pdf", text="FREIGHT CERTIFICATE\nOcean Freight: USD 500.00"),
        _file("some_bl.pdf", text="BILL OF LADING\nShipper: Acme Corp\nConsignee: Beta Ltd"),
    ]
    claims = [
        [_claim("inv", [1], evidence="invoice fields present")],
        [_claim("frt", [1], evidence="freight certificate")],
        [_claim("bl", [1], evidence="bill of lading")],
    ]
    result = _run(files, claims)
    assert {m["key"] for m in result[0]} == {"inv", "pl"}
    assert [m["key"] for m in result[1]] == ["frt"]
    assert [m["key"] for m in result[2]] == ["bl"]


def test_keyword_backstop_widening_a_files_breadth_can_lose_a_contested_slot():
    # A real, worth-knowing interaction with the PRE-EXISTING specificity rule (unrelated to
    # today's change): augmenting combined.pdf to claim BOTH Invoice and PackingList makes it
    # a BROADER claimant. If a genuinely separate, single-type invoice file competes for the
    # very same Invoice slot, the existing rule correctly prefers the narrower, more certain
    # claim - so combined.pdf can lose Invoice here while keeping PackingList. This is the
    # specificity rule behaving exactly as it already did before this feature existed; it is
    # not something the backstop should try to override.
    files = [
        _file("combined.pdf", text=HUSKY_STYLE_TEXT),
        _file("plain_invoice.pdf", text=ORDINARY_INVOICE_TEXT),
    ]
    claims = [
        [_claim("inv", [1], evidence="invoice fields present")],
        [_claim("inv", [1], evidence="invoice fields present")],
    ]
    result = _run(files, claims)
    assert [m["key"] for m in result[0]] == ["pl"]
    assert [m["key"] for m in result[1]] == ["inv"]


def test_keyword_backstop_never_fires_for_a_doc_type_with_no_signature_defined():
    # A BL slot exists and the file's own text obviously is not a BL, but "BL" has no
    # entry in the signature table at all - it must never be silently added.
    candidates_with_bl = [
        {"key": "inv", "name": "Invoice", "doc_type": "Invoice", "fields": []},
        {"key": "bl", "name": "BL", "doc_type": "BL", "fields": []},
    ]
    files = [_file("combined.pdf", text=HUSKY_STYLE_TEXT)]
    with patch("app.core.classifier.classify_document",
               side_effect=[[_claim("inv", [1], evidence="invoice fields present")]]):
        result = assign_documents_detailed(files, candidates_with_bl)
    assert "bl" not in {m["key"] for m in result[0]}


def test_keyword_backstop_rescues_a_file_the_model_matched_to_nothing():
    # A real bug this reproduces: the model's own classification call can simply miss a type
    # that is plainly there (an arrival notice stating real freight charges, matched to
    # nothing). Regex evidence does not need the model to have gotten anything else right
    # first - a file with strong signals for a covered type is rescued even from zero claims.
    files = [_file("combined.pdf", text=HUSKY_STYLE_TEXT, page_count=1)]
    result = _run(files, [[]])
    keys = {m["key"] for m in result[0]}
    assert keys == {"pl", "inv"}
    for m in result[0]:
        assert m["pages"] == [1]  # no partial match to anchor to - claims every page it has


def test_keyword_backstop_does_not_invent_a_type_for_a_file_with_no_real_signal():
    # A zero-match file whose text carries no strong signature for any covered type must stay
    # empty - the rescue is not a licence to guess whenever the model returns nothing.
    files = [_file("combined.pdf", text="Dear Sir, please find attached as requested.\nRegards,")]
    result = _run(files, [[]])
    assert result[0] == []


def test_keyword_backstop_reaches_a_custom_typed_slot_named_for_freight():
    # The exact real production shape: most tenants' freight slot is set up as doc_type
    # "Custom" with a name like "Fright Certificate" (their own spelling), never doc_type
    # "Freight" outright. Keying the backstop off the raw doc_type field checked nothing for
    # any of them - this is the same name-matching _hint_from_name already uses to describe
    # the slot to the model in the first place.
    candidates_custom_freight = [
        {"key": "inv", "name": "Invoice", "doc_type": "Invoice", "fields": []},
        {"key": "frt", "name": "Fright Certificate", "doc_type": "Custom", "fields": []},
    ]
    text = "ARRIVAL NOTICE\nORIGIN CHARGES : USD 216.74\nOCEAN FREIGHT : USD 143.415\n"
    files = [_file("arrival_notice.pdf", text=text, page_count=2)]
    with patch("app.core.classifier.classify_document", side_effect=[[]]):
        result = assign_documents_detailed(files, candidates_custom_freight)
    assert [m["key"] for m in result[0]] == ["frt"]
    assert result[0][0]["pages"] == [1, 2]


def test_keyword_backstop_rescues_a_freight_document_the_model_missed():
    # The exact real case: a forwarder's own arrival notice, correctly the customer's sample
    # for their "Freight Certificate" slot, states real freight charges but was matched to
    # nothing by the model's own call.
    text = (
        "ARRIVAL NOTICE\nTRACKING NO. 1077238006\n"
        "ORIGIN CHARGES : USD 216.74\nOCEAN FREIGHT : USD 143.415\n"
    )
    files = [_file("arrival_notice.pdf", text=text, page_count=2)]
    result = _run(files, [[]])
    assert [m["key"] for m in result[0]] == ["frt"]
    assert result[0][0]["pages"] == [1, 2]


# --------------------------------------------------------------------------- #
# _keyword_signature_match — direct unit tests on the matcher itself
# --------------------------------------------------------------------------- #

def test_signature_match_exact_threshold_boundary():
    # Required phrase + exactly 2 supporting markers (the minimum) -> match.
    text = "packing list\nnet weight: 5kg\ngross weight: 6kg"
    is_match, hits = _keyword_signature_match(text, "PackingList")
    assert is_match is True
    assert hits == 2


def test_signature_match_one_below_threshold_does_not_match():
    text = "packing list\nnet weight: 5kg"  # only 1 supporting marker
    is_match, hits = _keyword_signature_match(text, "PackingList")
    assert is_match is False
    assert hits == 1


def test_signature_match_required_phrase_missing_never_matches_regardless_of_supporting_count():
    # Every supporting marker present, but the required phrase itself is absent.
    text = "net weight: 5kg\ngross weight: 6kg\ndimension: 10x10x10\nmarks and nos: ABC\ntotal no of packs: 2"
    is_match, hits = _keyword_signature_match(text, "PackingList")
    assert is_match is False


def test_signature_match_invoice_exact_threshold_boundary():
    text = "invoice no: INV-1\ninvoice date: 1-Jan-2026\nunit price: 5.00"
    is_match, hits = _keyword_signature_match(text, "Invoice")
    assert is_match is True
    assert hits == 2


def test_signature_match_invoice_required_phrase_missing():
    text = "invoice date: 1-Jan-2026\nunit price: 5.00\namount in usd: 50.00\nhsn: 12345678"
    is_match, _ = _keyword_signature_match(text, "Invoice")
    assert is_match is False


def test_signature_match_is_case_insensitive():
    text = "PACKING LIST\nNET WEIGHT: 5KG\nGROSS WEIGHT: 6KG"
    is_match, _ = _keyword_signature_match(text, "PackingList")
    assert is_match is True


def test_signature_match_real_document_exact_phrase_no_and_kind_of_packages():
    # The EXACT real phrasing from the actual document this feature was built for:
    # "No. & kind of Packages" - not a simplified test string.
    text = (
        "SHIPPING INVOICE CUM PACKING LIST\n"
        "No. & kind of Packages: 1-WOODEN BOX\n"
        "Net Weight of Total Packs: 17.000 KGS\n"
        "Gross Weight of Total Packs: 22.000 KGS\n"
        "Dimension of each Pack: 620X420X380-1 IN MM\n"
    )
    is_match, hits = _keyword_signature_match(text, "PackingList")
    assert is_match is True
    assert hits >= 3


def test_signature_match_real_sansera_challan_neither_required_phrase_uses_the_word_literally():
    # Production text (via scratch_diagnose_sansera.py): titled "Commercial Invoice / Packing
    # Slip", its invoice number field reads "Commercial Inv No", its packing table header reads
    # "PACKING DETAILS" - literally never "invoice no" or "packing list".
    is_match, hits = _keyword_signature_match(SANSERA_STYLE_TEXT, "Invoice")
    assert is_match is True
    is_match, hits = _keyword_signature_match(SANSERA_STYLE_TEXT, "PackingList")
    assert is_match is True


def test_signature_match_unknown_doc_type_returns_false_without_crashing():
    # "BL" has no entry in the signature table at all (see
    # test_keyword_backstop_never_fires_for_a_doc_type_with_no_signature_defined).
    is_match, hits = _keyword_signature_match("packing list net weight gross weight", "BL")
    assert (is_match, hits) == (False, 0)


def test_signature_match_freight_real_arrival_notice_text():
    text = "ARRIVAL NOTICE\nORIGIN CHARGES : USD 216.74\nOCEAN FREIGHT : USD 143.415\n"
    is_match, hits = _keyword_signature_match(text, "Freight")
    assert is_match is True
    assert hits >= 1


def test_signature_match_freight_required_phrase_missing():
    # A bill of lading's own "Freight Payable at Destination" box mentions "freight" only in
    # passing - it must not, on its own, satisfy the Freight signature.
    text = "Freight Payable at Destination\nPort of Discharge: Chennai"
    is_match, _ = _keyword_signature_match(text, "Freight")
    assert is_match is False


def test_signature_match_none_and_empty_text_do_not_crash():
    assert _keyword_signature_match("", "PackingList") == (False, 0)
    assert _keyword_signature_match(None, "PackingList") == (False, 0)


# --------------------------------------------------------------------------- #
# classify_document — a page with no extractable OCR text must never be handed
# to the model as an image to guess from. Program-based check only, never AI.
# --------------------------------------------------------------------------- #

def _settings_with_key():
    s = MagicMock()
    s.openai_api_key = "fake-key-for-test"
    s.openai_model = "gpt-4o-mini"
    return s


def test_classify_document_returns_empty_for_blank_text_without_calling_openai():
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI") as mock_openai_cls:
        result = classify_document("scan.png", "", MagicMock(exists=lambda: True), CANDIDATES)
    assert result == []
    mock_openai_cls.assert_not_called()


def test_classify_document_returns_empty_for_none_text_without_calling_openai():
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI") as mock_openai_cls:
        result = classify_document("scan.png", None, MagicMock(exists=lambda: True), CANDIDATES)
    assert result == []
    mock_openai_cls.assert_not_called()


def test_classify_document_returns_empty_for_whitespace_only_text_without_calling_openai():
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI") as mock_openai_cls:
        result = classify_document("scan.png", "   \n\t  ", MagicMock(exists=lambda: True), CANDIDATES)
    assert result == []
    mock_openai_cls.assert_not_called()


def test_classify_document_ignores_a_real_image_when_text_is_blank():
    # The old behaviour (removed): fall back to sending the page image itself for a vision
    # guess when OCR text was empty. This confirms that path is gone even when a real,
    # existing image file is available - having an image must never revive the AI call.
    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI") as mock_openai_cls, \
         patch("pathlib.Path.exists", return_value=True), \
         patch("pathlib.Path.read_bytes", return_value=b"fake png bytes"):
        from pathlib import Path
        result = classify_document("scan.png", "", Path("/fake/page_1.png"), CANDIDATES)
    assert result == []
    mock_openai_cls.assert_not_called()


def test_classify_document_still_calls_openai_when_real_text_is_present():
    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = (
        '{"matches": [{"key": "inv", "pages": [1], "evidence": "Invoice No: INV-123, Total Payable: 500"}]}'
    )
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = fake_response

    with patch("app.core.classifier.get_settings", return_value=_settings_with_key()), \
         patch("openai.OpenAI", return_value=mock_client) as mock_openai_cls:
        result = classify_document("invoice.pdf", "Invoice No: INV-123\nTotal Payable: 500", None, CANDIDATES)

    mock_openai_cls.assert_called_once()
    assert [m["key"] for m in result] == ["inv"]


# ---------------------------------------------------------------------------
# Deterministic evidence outranks the breadth heuristic
# ---------------------------------------------------------------------------

def test_a_keyword_signature_claim_survives_the_specificity_rule():
    """The rule must not strip a well-evidenced claim and keep a guess.

    Seen in production on a forwarder's arrival notice. The model claimed it as an
    Invoice - it carries "Total Payable" and a charge table - and the keyword
    backstop claimed it as Freight, which is what it actually is. Holding two
    slots made it "broad", so the specificity rule stripped Freight in favour of a
    file holding one slot, and left the Invoice claim standing because only the
    evidenced claim was eligible to be stripped.

    The arrival notice then sat in the Invoice slot. Breadth is a heuristic about
    how widely a file claimed; a keyword signature is the document's own words
    matching a required phrase plus supporting markers. Where they disagree, the
    evidence wins.
    """
    from app.core.classifier import assign_documents_detailed

    candidates = [
        {"key": "inv", "name": "Invoice", "doc_type": "Invoice", "fields": []},
        {"key": "frt", "name": "Fright Certificate", "doc_type": "Custom", "fields": []},
    ]
    # Two claims on the arrival notice: a broad model guess, and the backstop's
    # signature. The competing file claims one slot, so the rule would normally
    # strip the arrival notice's Freight claim.
    claims = [
        [
            {"key": "inv", "pages": [1], "evidence": "total payable", "source": "model"},
            {"key": "frt", "pages": [1], "evidence": "ocean freight usd 1,250.00",
             "source": "signature"},
        ],
        [{"key": "frt", "pages": [1], "evidence": "freight certificate", "source": "model"}],
    ]

    import app.core.classifier as mod

    original = mod.classify_document
    mod.classify_document = lambda *a, **k: claims.pop(0)
    try:
        files = [
            {"name": "arrival_notice.pdf", "text": "x", "image": None, "page_count": 1},
            {"name": "freight_cert.pdf", "text": "y", "image": None, "page_count": 1},
        ]
        result = assign_documents_detailed(files, candidates)
    finally:
        mod.classify_document = original

    arrival_keys = {m["key"] for m in result[0]}
    assert "frt" in arrival_keys, "the evidenced Freight claim was stripped"


def test_an_unevidenced_claim_is_still_stripped():
    """The exemption must not disable the rule it is an exception to.

    A file claiming a slot only because the model said so still loses that slot to
    a file that claimed it more specifically - that is the rule's whole purpose,
    and it is what stops one invoice also filling the packing list slot.
    """
    from app.core.classifier import assign_documents_detailed
    import app.core.classifier as mod

    candidates = [
        {"key": "inv", "name": "Invoice", "doc_type": "Invoice", "fields": []},
        {"key": "pl", "name": "Packing List", "doc_type": "PackingList", "fields": []},
    ]
    claims = [
        [
            {"key": "inv", "pages": [1], "evidence": "invoice no 1", "source": "model"},
            {"key": "pl", "pages": [1], "evidence": "vague packing wording", "source": "model"},
        ],
        [{"key": "pl", "pages": [1], "evidence": "packing list", "source": "model"}],
    ]
    original = mod.classify_document
    mod.classify_document = lambda *a, **k: claims.pop(0)
    try:
        files = [
            {"name": "broad.pdf", "text": "x", "image": None, "page_count": 1},
            {"name": "packing_list.pdf", "text": "y", "image": None, "page_count": 1},
        ]
        result = assign_documents_detailed(files, candidates)
    finally:
        mod.classify_document = original

    assert {m["key"] for m in result[0]} == {"inv"}, "the broad claimant kept a slot it should have lost"
    assert {m["key"] for m in result[1]} == {"pl"}
