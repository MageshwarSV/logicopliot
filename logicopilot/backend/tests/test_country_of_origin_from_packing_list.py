"""A product with no country of origin on the invoice takes it from the packing list.

Per PRODUCT, and matched on the PART NUMBER - never on the line's position.

Built from JOB-7C8DA9 (ROHM air, production, 2026-11-25), where all 34 products came out
blank. The invoice carries no per-product origin at all, only one header line reading
"ORIGIN: Made in Malaysia,Philippine,Thailand,China". The packing list prints it under every
product: "(Made In Philippine-QTY:15000)".

The trap this exists to avoid: the two documents are ordered differently. The invoice groups
by P/O number and HS code, the packing list by carton. Invoice line 1 is KDZVTFTR43A, which
is MALAYSIA; packing list line 1 is LTR10EZPF51R0, which is PHILIPPINE. Cargora's existing
line merge and `fallback_fields` both match on position, so either would have filed PH against
a product that is MY. A blank is caught by the operator and by the ERP; a plausible wrong
country is filed.

Measured on production: 46 of 1,071 product lines are blank, across 4 jobs.
"""
from app.core.line_fallback import apply_line_fallbacks, fill_blanks_by_key

# Verbatim from the two documents on JOB-7C8DA9, in each document's own order.
INVOICE_PARTS = ["9404000733M02", "9401009239M01", "9990933672", "9404000540M01"]
#                 KDZVTFTR43A      LTR18EZPJ304    MCR03EZPFX3162  RB160MM-40TFTR
PL_PARTS = ["9401008778M01", "9990933684", "9404000733M02", "9404000540M01", "9990933672"]
PL_ORIGIN = ["PH", "PH", "MY", "MY", "TH"]
#             LTR10EZPF51R0  MCR10...    KDZVTFTR43A   RB160MM...   MCR03EZPFX3162


def _rows(invoice_origins):
    return {
        "CountryOfOriginCode": list(invoice_origins),
        "item_material_code": list(INVOICE_PARTS),
        "pl_part_code": list(PL_PARTS),
        "pl_origin": list(PL_ORIGIN),
    }


def test_the_production_job_fills_every_blank_with_its_own_products_origin():
    rows = _rows(["", "", "", ""])
    assert fill_blanks_by_key(
        rows, target="CountryOfOriginCode", key="item_material_code",
        source_value="pl_origin", source_key="pl_part_code", expect_len=2) == 3
    # KDZVTFTR43A is MY, MCR03EZPFX3162 is TH, RB160MM-40TFTR is MY.
    # LTR18EZPJ304 is not on the packing list at all, so it stays blank.
    assert rows["CountryOfOriginCode"] == ["MY", "", "TH", "MY"]


def test_it_does_not_match_on_position():
    """THE regression. Position would give line 1 the packing list's first row, PH, when
    KDZVTFTR43A is made in Malaysia."""
    rows = _rows(["", "", "", ""])
    apply_line_fallbacks(rows)
    assert rows["CountryOfOriginCode"][0] == "MY", (
        "line 1 must take ITS OWN part's origin, not the packing list's first row")
    assert rows["CountryOfOriginCode"][0] != PL_ORIGIN[0]


def test_a_value_the_invoice_carried_is_never_overwritten():
    """The invoice is the source of truth. The packing list only fills what it left blank -
    even when the two documents disagree, which is the operator's call, not ours."""
    rows = _rows(["CN", "", "", ""])
    fill_blanks_by_key(
        rows, target="CountryOfOriginCode", key="item_material_code",
        source_value="pl_origin", source_key="pl_part_code", expect_len=2)
    assert rows["CountryOfOriginCode"][0] == "CN", "the invoice's own CN must survive"


def test_a_product_the_packing_list_does_not_list_stays_blank():
    rows = _rows(["", "", "", ""])
    apply_line_fallbacks(rows)
    assert rows["CountryOfOriginCode"][1] == "", (
        "LTR18EZPJ304 is on the invoice only - it must be left for the operator")


def test_a_part_with_two_different_origins_is_left_blank():
    """A genuine ambiguity on the document itself. Guessing between two countries is exactly
    what this module exists to avoid."""
    rows = _rows(["", "", "", ""])
    rows["pl_part_code"].append("9404000733M02")   # KDZVTFTR43A again...
    rows["pl_origin"].append("TH")                 # ...with a different country
    apply_line_fallbacks(rows)
    assert rows["CountryOfOriginCode"][0] == ""


def test_the_same_part_listed_twice_with_the_SAME_origin_still_fills():
    """ROHM lists MCR10EZPF10R0 on two carton rows, both Philippine. That is not ambiguity."""
    rows = _rows(["", "", "", ""])
    rows["pl_part_code"].append("9404000733M02")
    rows["pl_origin"].append("MY")                 # the same country again
    apply_line_fallbacks(rows)
    assert rows["CountryOfOriginCode"][0] == "MY"


def test_a_value_of_the_wrong_width_is_not_written():
    """The ERP refuses the WHOLE import over this - "ITEMS Country_of_Origin ... Entry should
    be of 2 characters" rejected four jobs on 2026-11-26. A blank the operator fixes beats an
    import that will not go in at all."""
    rows = _rows(["", "", "", ""])
    rows["pl_origin"] = ["PH", "PH", "Malaysia", "MY", "TH"]   # a country NAME, not a code
    apply_line_fallbacks(rows)
    assert rows["CountryOfOriginCode"][0] == "", "'Malaysia' is 8 characters - do not write it"
    assert rows["CountryOfOriginCode"][2] == "TH", "the well-formed ones still fill"


def test_punctuation_and_spacing_in_the_part_number_still_match():
    """The invoice prints "(9404000733M02)" and OCR may split it. Letters and digits decide."""
    rows = _rows(["", "", "", ""])
    rows["item_material_code"][0] = " (9404000733 m02) "
    apply_line_fallbacks(rows)
    assert rows["CountryOfOriginCode"][0] == "MY"


def test_it_is_inert_without_the_packing_list_marks():
    """Every template whose packing list is not marked for this must behave exactly as it does
    today - which is every template on production right now."""
    rows = {"CountryOfOriginCode": ["", ""], "item_material_code": ["A", "B"]}
    before = {k: list(v) for k, v in rows.items()}
    assert apply_line_fallbacks(rows) == {}
    assert rows == before


def test_a_job_with_no_line_items_at_all_is_untouched():
    assert apply_line_fallbacks({}) == {}


def test_a_blank_part_number_on_the_packing_list_is_ignored():
    """A carton row that read nothing must not become a wildcard that matches every product."""
    rows = _rows(["", "", "", ""])
    rows["pl_part_code"] = ["", "", "", "", ""]
    apply_line_fallbacks(rows)
    assert rows["CountryOfOriginCode"] == ["", "", "", ""]


def test_a_packing_list_longer_than_the_invoice_does_not_add_product_lines():
    """The other document's table has its own length. Filling must never change how many
    products the job has."""
    rows = _rows(["", "", "", ""])
    apply_line_fallbacks(rows)
    assert len(rows["CountryOfOriginCode"]) == 4 == len(rows["item_material_code"])
