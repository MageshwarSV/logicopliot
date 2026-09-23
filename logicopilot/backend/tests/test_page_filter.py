"""classify_page / filter_pages: drop carrier terms-and-conditions pages before
classification and extraction, without ever losing a real data page."""

from app.core.page_filter import classify_page, filter_pages, kept_page_to_original

# A real excerpt matching the exact Blue Anchor Line boilerplate this filter was built
# for (from a real customer's sea waybill), trimmed but keeping enough signature phrases.
BLUE_ANCHOR_TERMS_PAGE = """
1. DEFINITIONS
"Carrier" means Transpac Container System Pte. Ltd., 5 Temasek Boulevard, trading as
Blue Anchor Line.
2. CONTRACTING PARTIES
3. CARRIER'S TARIFF
4. NON-NEGOTIABILITY
Notwithstanding the application to this sea waybill of the Hague Rules, or the Hague-Visby
Rules, this sea waybill is not negotiable.
5. SUB-CONTRACTING AND INDEMNITIES
The Merchant shall Indemnify the Carrier against all consequences thereof, howsoever
caused, of whatsoever nature, and the burden of proof and limitation of liability shall
rest as set out herein.
6. CARRIER'S LIABILITY
21. JURISDICTION AND LAW
Disputes arising under this sea waybill shall be determined by the courts of London,
United Kingdom, and the Both-to-Blame Collision clause and General Average provisions
under Port to Port Transport shall apply, as the servant or agent of the Carrier.
"""

# A genuine data page: dense with numbers, short, no clause structure.
REAL_BL_DATA_PAGE = """
Shipper: DTDS TECHNOLOGY PTE LTD, Blk 19 Kallang Ave, #05-153, Singapore 339410
Consignee: FLEXTRONICS INTERNATIONAL MANAGEMENT SERVICES LTD
MAWB: 160-CTU-16187986   HAWB No: CTUA2605766
Port of Loading: CHENGDU   Port of Discharge: CHENNAI
Gross Weight: 3.56 Kg   Net Weight: 1.78 Kg
Invoice No: DSMI-28082026   Invoice Date: 28/08/2026
"""

# A page that legitimately mentions ONE of the signature phrases but is still a real data
# page - must NOT be dropped (the freight certificate / BL form false-positive case).
FREIGHT_CERT_MENTIONING_DANGEROUS_GOODS = """
Freight Certificate
Shipment does not contain DANGEROUS GOODS.
Ocean Freight: USD 850.00
Destination Handling Fee: USD 120.00
Total: USD 970.00
"""


def test_classify_page_flags_real_terms_page_via_signature():
    is_boiler, why = classify_page(BLUE_ANCHOR_TERMS_PAGE)
    assert is_boiler is True
    assert "signature" in why


def test_classify_page_does_not_flag_a_real_data_page():
    is_boiler, why = classify_page(REAL_BL_DATA_PAGE)
    assert is_boiler is False
    assert why == ""


def test_classify_page_does_not_flag_one_incidental_signature_word():
    # One hit ("DANGEROUS GOODS") must sit well below the threshold (6) - this is exactly
    # the case the threshold was chosen to protect.
    is_boiler, _ = classify_page(FREIGHT_CERT_MENTIONING_DANGEROUS_GOODS)
    assert is_boiler is False


def test_classify_page_handles_empty_and_whitespace():
    assert classify_page("") == (False, "")
    assert classify_page("   \n\t  ") == (False, "")


def test_classify_page_curly_quotes_still_match_signature_patterns():
    # The real document uses curly apostrophes ("Carrier’s Tariff") - _normalise must
    # straighten them before the regex patterns (written with straight quotes) can match.
    text = BLUE_ANCHOR_TERMS_PAGE.replace("Carrier's", "Carrier’s")
    is_boiler, _ = classify_page(text)
    assert is_boiler is True


def test_filter_pages_drops_only_the_terms_page_and_renumbers():
    pages = [REAL_BL_DATA_PAGE, BLUE_ANCHOR_TERMS_PAGE, REAL_BL_DATA_PAGE]
    kept, dropped = filter_pages(pages, "test waybill")
    assert dropped == [2]
    assert len(kept) == 2
    assert all("DEFINITIONS" not in k for k in kept)


def test_filter_pages_keeps_everything_when_nothing_is_boilerplate():
    pages = [REAL_BL_DATA_PAGE, FREIGHT_CERT_MENTIONING_DANGEROUS_GOODS]
    kept, dropped = filter_pages(pages, "test doc")
    assert dropped == []
    assert kept == pages


def test_filter_pages_never_drops_every_page_even_if_all_look_like_terms():
    # Safety net: a document that is ENTIRELY boilerplate (or a false-positive run of
    # them) must stand down rather than return nothing at all.
    pages = [BLUE_ANCHOR_TERMS_PAGE, BLUE_ANCHOR_TERMS_PAGE]
    kept, dropped = filter_pages(pages, "all terms")
    assert dropped == []
    assert kept == pages


def test_filter_pages_with_empty_list_is_a_noop():
    assert filter_pages([], "empty") == ([], [])


def test_filter_pages_stands_down_when_the_only_real_content_is_all_boilerplate():
    # A blank page (failed OCR) plus one real terms page: the ONLY non-blank page is
    # boilerplate, so dropping it would leave nothing usable at all - the safety net
    # must stand down here, same as the all-boilerplate case above.
    pages = ["", BLUE_ANCHOR_TERMS_PAGE]
    kept, dropped = filter_pages(pages, "mixed")
    assert dropped == []
    assert kept == pages


def test_filter_pages_drops_terms_page_alongside_an_unrelated_blank_page():
    # Same shape, but now there IS a second real (non-blank) page - the blank page must
    # not artificially inflate the "all content" count and block a legitimate drop.
    pages = ["", BLUE_ANCHOR_TERMS_PAGE, REAL_BL_DATA_PAGE]
    kept, dropped = filter_pages(pages, "mixed2")
    assert dropped == [2]
    assert kept == ["", REAL_BL_DATA_PAGE]


def test_kept_page_to_original_maps_around_a_dropped_middle_page():
    # 3 original pages, page 2 dropped -> the kept list's two entries were originally
    # pages 1 and 3.
    assert kept_page_to_original(3, [2]) == [1, 3]


def test_kept_page_to_original_with_nothing_dropped_is_identity():
    assert kept_page_to_original(4, []) == [1, 2, 3, 4]


def test_kept_page_to_original_with_everything_dropped_is_empty():
    assert kept_page_to_original(2, [1, 2]) == []


def test_filter_pages_unaffected_when_extra_classifier_is_omitted():
    # Regression guard for the extra_classifier addition (app/core/custom_page_filter.py):
    # every existing call site omits it, so behaviour with the default None must be
    # byte-for-byte identical to before this parameter existed.
    pages = [REAL_BL_DATA_PAGE, BLUE_ANCHOR_TERMS_PAGE, REAL_BL_DATA_PAGE]
    assert filter_pages(pages, "test waybill") == filter_pages(pages, "test waybill", extra_classifier=None)


def test_filter_pages_extra_classifier_drops_a_page_the_builtin_rule_misses():
    extra = lambda t: (True, "custom match") if "SECRET BOILERPLATE" in t else (False, "")
    pages = [REAL_BL_DATA_PAGE, "SECRET BOILERPLATE PAGE", REAL_BL_DATA_PAGE]
    kept, dropped = filter_pages(pages, "test doc", extra_classifier=extra)
    assert dropped == [2]
    assert kept == [REAL_BL_DATA_PAGE, REAL_BL_DATA_PAGE]


def test_filter_pages_extra_classifier_not_consulted_when_builtin_rule_already_matches():
    # The built-in rule already flags this page; extra_classifier must not run redundantly
    # (and, if it somehow disagreed, the built-in verdict wins since it's tried first).
    calls = []

    def extra(t):
        calls.append(t)
        return False, ""

    filter_pages([BLUE_ANCHOR_TERMS_PAGE], "test doc", extra_classifier=extra)
    assert calls == []
