"""One place that knows a tariff classification code by every name a document gives it.

The same eight-or-so digits are printed under a different heading on almost every document
that carries them: CTH on an Indian bill of entry, HS Code on a supplier's commercial
invoice, HSN on a GST invoice, HTS on anything drawn up in the United States, RITC or ITC-HS
on a clearing agent's own paperwork. These are not different fields - they are ONE field with
a dozen captions.

A field configured as "Document CTH" read a column headed "CTH" and returned nothing whenever
the supplier had headed the identical column "HTS CODE", because nothing in the prompt said
those are the same question. The operator was then shown "Not Found" beside a code printed on
the page in front of them, and had to type it in by hand on every product line.

Lives in its own module rather than inside extraction.py so llm.py can use it too:
extraction.py already imports llm.py, so a constant shared from there could only be reached
by making that dependency circular.
"""

import re

# Every caption the reader is told to accept. Spelled out in full rather than left to the
# model's own sense of what is "close enough", because the failure this exists to stop is
# precisely the model deciding that a heading it was not shown must be some other field.
TARIFF_CODE_CAPTIONS = (
    "CTH", "CTH Code", "CTH No", "Customs Tariff Heading", "Tariff Heading",
    "Tariff Code", "Tariff No", "HS Code", "H.S. Code", "HS", "HS No",
    "HSN", "HSN Code", "HTS", "HTS Code", "HTSUS", "RITC", "RITC No", "RITC Code",
    "ITC-HS", "ITC HS Code", "Commodity Code", "Harmonized Code", "Harmonised Code",
)

# Matched against a field's OWN NAME, never against its prompt text. A prompt routinely says
# what a field is NOT - "Do NOT confuse this with the customs tariff heading" is in one of
# these very prompts - and matching on that would attach tariff guidance to the one field
# that must never carry it.
#
# Every alternative is anchored with \b at both ends. That is what keeps "IsHSS", a real
# hardcoded field on these templates, from reading as an HS field.
_TARIFF_FIELD = re.compile(
    r"\b("
    r"cth"
    r"|hts(us)?"
    r"|hsn"
    r"|h\.?\s*s\.?\s*(code|no)"
    r"|hs"
    r"|ritc"
    r"|itc[\s._-]*hs"
    r"|harmoni[sz]ed\s*code"
    r"|commodity\s*code"
    r"|(customs\s*)?tariff\s*(code|heading|no|number)"
    r")\b",
    re.IGNORECASE,
)


def is_tariff_code_field(label: str | None) -> bool:
    """True when a field's NAME says it carries a tariff classification code."""
    return bool(label and _TARIFF_FIELD.search(label))


# Built from the tuple above so the list the model is given and the list this module claims
# to support cannot drift apart.
TARIFF_CODE_SYNONYMS = (
    "THIS FIELD IS THE TARIFF CLASSIFICATION CODE, AND DOCUMENTS PRINT IT UNDER MANY "
    "DIFFERENT HEADINGS. Every one of these is a name for the SAME field, and ANY of them "
    "answers it: " + ", ".join(TARIFF_CODE_CAPTIONS) + ". Read whichever heading this "
    "document actually uses, and never return null merely because the caption is not worded "
    "the way this field happens to be named. This widens which HEADINGS count, never "
    "whether a code has to be there at all: a line that genuinely shows none is still "
    "empty, and must never be filled from another line or from prior knowledge. "
    "Where a line carries more than one of them, "
    "prefer the caption closest to this field's own name. Return ONLY the code itself - "
    "never the caption, never the goods description, and never a country-of-origin code "
    "printed beside it."
)
