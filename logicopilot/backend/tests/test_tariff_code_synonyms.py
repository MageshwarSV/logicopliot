"""A tariff code is the same number printed under a dozen different captions.

Operators were shown "Not Found" for a CTH that was printed on the page in front of them,
because the supplier had headed that column "HTS CODE" and the reader had only ever been
told to look for a "CTH". These tests pin the synonym list to the names that actually turn
up on these documents, and pin WHERE it is attached: to a field whose own NAME says it
carries a tariff code, and to nothing else.
"""
import pytest

from app.core.extraction import _field_lines
from app.core.llm import _instruction
from app.core.tariff_codes import (
    TARIFF_CODE_CAPTIONS,
    TARIFF_CODE_SYNONYMS,
    is_tariff_code_field,
)


@pytest.mark.parametrize("label", [
    # The fields configured on the live templates today...
    "CTH", "Document CTH", "Dump CTH Number", "Product CTH no", "RITC No.",
    "hs code", "HS Code", "HSN code",
    # ...and the rest of the names a document can use for the same column.
    "cth code", "H.S. Code", "HS", "HSN", "HTS", "HTS Code", "HTSUS", "RITC",
    "ITC-HS Code", "ITC HS", "Tariff Code", "Customs Tariff Heading",
    "Commodity Code", "Harmonized Code", "Harmonised Code",
])
def test_a_tariff_field_is_recognised_by_any_of_its_names(label):
    assert is_tariff_code_field(label)


@pytest.mark.parametrize("label", [
    # IsHSS is a real hardcoded field on these templates. "HSS" is a high-seas sale, not an
    # HS code, and the \b anchors are the only thing keeping the two apart.
    "IsHSS",
    "Product Description", "Combined Description", "Item Quantity", "Unit Price",
    "Country Of Origin", "CountryOfOriginCode", "Invoice Number", "Gross Weight",
    "shipper", "Customer Part code", "item_material_code", "Purchase Order Number",
    "", None,
])
def test_an_ordinary_field_is_not_mistaken_for_one(label):
    assert not is_tariff_code_field(label)


def test_every_name_the_customer_asked_for_reaches_the_model():
    # Verbatim from the request: "there can be these hts code, hs code, hsn code, ritc code,
    # itc-hs code, cth code".
    for name in ("CTH", "HS Code", "HSN", "HTS", "RITC", "ITC-HS"):
        assert name in TARIFF_CODE_SYNONYMS, name


def test_the_guidance_lists_every_caption_the_module_claims_to_support():
    # The prose is built from the tuple, so this is really a guard against someone later
    # hand-editing one and not the other.
    for caption in TARIFF_CODE_CAPTIONS:
        assert caption in TARIFF_CODE_SYNONYMS, caption


def test_a_mark_named_as_a_tariff_code_is_told_the_other_captions():
    # Covers the "hs code" / "HSN code" marks, read through extract_document_rows.
    line = _field_lines([{"label": "HS Code", "prompt": "Return the HS code."}])[0]
    assert "HTS Code" in line
    assert "RITC" in line
    assert "ITC-HS" in line


def test_an_ordinary_mark_is_not():
    line = _field_lines([{"label": "Product Description", "prompt": "Return it."}])[0]
    assert TARIFF_CODE_SYNONYMS not in line
    assert "HTS Code" not in line


def test_document_cth_carries_the_captions_into_its_own_instruction():
    # The exact field from the screenshots: a per-row AI custom field computed by
    # compute_custom_field_per_row, which never goes anywhere near _field_lines. Covering
    # only the marks path would have left this one - the one actually reported - unfixed.
    text = _instruction("Return the CTH printed on the document.", "Document CTH")
    assert "HTS Code" in text
    assert "ITC-HS" in text
    assert text.startswith("Instruction: Return the CTH printed on the document.")


def test_a_field_whose_PROMPT_merely_mentions_a_tariff_code_does_not():
    # Document CTH's own live prompt says "Do NOT confuse this with the customs tariff
    # heading". Keying on prompt text instead of on the field's name would have pinned the
    # caption list onto the very fields written to stay away from it.
    text = _instruction(
        "Return the goods description. Do NOT confuse this with the CTH or the HS code.",
        "Product Description",
    )
    assert TARIFF_CODE_SYNONYMS not in text
