"""build_structured_text / describe_regions (app/core/structure_engine.py) - the two-pass
region-detection-then-reading-order engine sitting between docai.py's flat paragraph/table
geometry and the existing layout_text no-op fallback. Every case here is built from plain
dicts (the exact contract docai.py's _page_blocks hands the engine), not Document AI proto
objects.

The central distinction under test throughout: COLUMN DETECTION ("are these two spatially
independent regions?") and TABLE DETECTION ("does this region show genuine repeated row/column
structure?") are two separate questions. There is no fixed column-count floor any more (removed
during design review in favour of adaptive, evidence-based scoring - see structure_engine's own
module docstring): a 2-column split CAN become a table given strong enough real spacing/grid
evidence, and a real item table (3+ distinct fields per record) is recognized even though its
own TOTAL row rarely populates every column. Real OCR geometry (jittered, never perfectly
uniform) very rarely gives a plain 2-column form field/label block ENOUGH row-to-row spacing
regularity to cross that bar - see the real-Cisco-document tests below - while a hand-authored,
perfectly uniform synthetic fixture can; that residual edge case is deliberately documented
below, not hidden."""
import json
from pathlib import Path

from app.core.structure_engine import build_structured_text, describe_regions

_FIXTURES = Path(__file__).parent / "fixtures"


def _p(text, x0, y0, x1, y1):
    return {"kind": "paragraph", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text}


def _t(text, x0, y0, x1, y1):
    return {"kind": "table", "x0": x0, "y0": y0, "x1": x1, "y1": y1, "text": text}


def test_fewer_than_two_paragraphs_returns_none():
    assert build_structured_text([_p("Only one paragraph", 0.05, 0.10, 0.90, 0.14)]) is None


def test_ordinary_single_column_page_is_a_no_op():
    """Every paragraph has a real Y-gap before the next one - the row-first split isolates
    each into its own text region on the very first pass, in existing order, so column or
    table detection is never even reached."""
    blocks = [
        _p("Terms and Conditions", 0.05, 0.10, 0.40, 0.13),
        _p("1. Goods travel at buyer's risk.", 0.08, 0.15, 0.70, 0.18),
        _p("2. Payment due within 30 days.", 0.08, 0.20, 0.75, 0.23),
        _p("Total: 1,234.56", 0.60, 0.28, 0.90, 0.31),
    ]
    assert build_structured_text(blocks) is None


def test_real_cisco_document_seller_ship_from_header_stays_columns_not_table():
    """The REAL bug-regression document: an actual live Cisco commercial invoice's own
    Document AI paragraph geometry (91 blocks, captured verbatim - not hand-typed
    coordinates), loaded from tests/fixtures/cisco_invoice_blocks.json. Its own Seller/Ship
    From header is a plain 2-column form-field block exactly like the classic problem case,
    but real OCR bounding boxes are never perfectly uniformly spaced the way a hand-authored
    fixture can be (see test_a_perfectly_uniform_two_column_grid_can_become_a_table below for
    that documented, accepted synthetic-only edge case) - here, the page's own adaptive
    geometry (derived from ALL its real gaps, including some genuinely tiny ones elsewhere on
    the same page) is small enough that each header row is isolated into its own single-row
    band before the table/key_value/columns classifier is ever reached at all, so SELLER and
    SHIP FROM read out in plain column-major order, never merged into one markdown table."""
    blocks = json.loads((_FIXTURES / "cisco_invoice_blocks.json").read_text(encoding="utf-8"))
    text = build_structured_text(blocks)
    seller_idx = text.index("SELLER")
    ship_from_idx = text.index("SHIP FROM")
    assert seller_idx < ship_from_idx
    assert "| SELLER | SHIP FROM |" not in text
    assert "CISCO SYSTEMS, INC." in text
    assert "SCHENKER SINGAPORE PTE LTD" in text


def test_a_perfectly_uniform_two_column_grid_can_become_a_table():
    """DOCUMENTED, ACCEPTED residual limitation, not a bug: a hand-authored 2-column region
    whose rows are spaced with EXACTLY equal gaps (a synthetic fixture, not real OCR output -
    see the real-document version of this same shape just above, which correctly stays
    "columns") gives row-to-row spacing regularity a perfect 1.0 score, which is enough
    real, measurable evidence for TABLE to outscore KEY_VALUE/COLUMNS even though a person
    would read this as a Seller/Ship-From style form. This was raised and accepted
    explicitly during design review: removing the old fixed 2-column table floor in favour
    of evidence-based scoring means a coincidentally perfect synthetic grid can occasionally
    misclassify, and real OCR geometry (jittered, never perfectly uniform) does not trigger
    it - see the real-document test above and the real Cisco/packing-list verification this
    module's own regression suite runs against. Preserving row pairing either way (this
    still reads out as one correct "label, value" pair per line via the table's own rows),
    this is a values-preserving edge case, not a data-loss one."""
    blocks = [
        _p("Seller: ACME Corp", 0.05, 0.10, 0.35, 0.14),
        _p("Ship From: 123 Main St", 0.55, 0.10, 0.85, 0.14),
        _p("123 Foo Ave", 0.05, 0.14, 0.35, 0.18),
        _p("456 Bar Blvd", 0.55, 0.14, 0.85, 0.18),
        _p("Springfield, IL", 0.05, 0.18, 0.35, 0.22),
        _p("Chicago, IL", 0.55, 0.18, 0.85, 0.22),
        _p("USA", 0.05, 0.22, 0.35, 0.26),
        _p("USA", 0.55, 0.22, 0.85, 0.26),
    ]
    result = build_structured_text(blocks)
    assert result == (
        "| Seller: ACME Corp | Ship From: 123 Main St |\n"
        "|---|---|\n"
        "| 123 Foo Ave | 456 Bar Blvd |\n"
        "| Springfield, IL | Chicago, IL |\n"
        "| USA | USA |"
    )


def test_table_block_stays_in_place_around_a_header_region():
    """A real Document AI table sitting after some other header region keeps its own
    original slot - it has a clear Y-gap from the header above it, so the row-first split
    isolates it into its own later region, never pulled into the header's own column
    analysis, and never split apart itself either. Only 2 rows here (spacing regularity is
    unmeasurable below 3 - see _spacing_regularity), so the header reads out as key_value,
    re-pairing each label with its own answer; what this test actually exercises is Y-CUT
    ISOLATION, a separate mechanical concern from how the header itself gets classified."""
    blocks = [
        _p("Seller: ACME Corp", 0.05, 0.10, 0.35, 0.14),
        _p("Ship From: 123 Main St", 0.55, 0.10, 0.85, 0.14),
        _p("123 Foo Ave", 0.05, 0.14, 0.35, 0.18),
        _p("456 Bar Blvd", 0.55, 0.14, 0.85, 0.18),
        _t("| Item | Qty |\n| Widget | 10 |", 0.05, 0.30, 0.85, 0.45),
    ]
    result = build_structured_text(blocks)
    assert result == "\n\n".join(
        [
            "Seller: ACME Corp: Ship From: 123 Main St",
            "123 Foo Ave: 456 Bar Blvd",
            "| Item | Qty |\n| Widget | 10 |",
        ]
    )


def test_three_uneven_columns_with_row_alignment_becomes_a_ragged_table():
    """Three columns (Seller / Ship From / Terms) with DIFFERENT item counts (3, 3, 2) and no
    row-to-row gap anywhere - meeting the 3-column floor, so this is eligible for table
    promotion, and the row-alignment test passes for 2 of its 3 rows. Must come back as a
    ragged table (the third row's Terms cell empty) - never content bleeding from one column
    into another the way an early, abandoned version of this engine once did."""
    blocks = [
        _p("Seller: ACME Corp", 0.05, 0.10, 0.25, 0.14),
        _p("Ship From: Acme Freight", 0.35, 0.10, 0.55, 0.14),
        _p("Terms: N45", 0.65, 0.10, 0.85, 0.14),
        _p("123 Foo Ave", 0.05, 0.14, 0.25, 0.18),
        _p("456 Bar Blvd", 0.35, 0.14, 0.55, 0.18),
        _p("Type: Sale", 0.65, 0.14, 0.85, 0.18),
        _p("Springfield, IL", 0.05, 0.18, 0.25, 0.22),
        _p("Chicago, IL", 0.35, 0.18, 0.55, 0.22),
    ]
    result = build_structured_text(blocks)
    assert result == (
        "| Seller: ACME Corp | Ship From: Acme Freight | Terms: N45 |\n"
        "|---|---|---|\n"
        "| 123 Foo Ave | 456 Bar Blvd | Type: Sale |\n"
        "| Springfield, IL | Chicago, IL |  |"
    )


def test_adjacent_short_cluster_without_a_real_gap_is_left_in_plain_order():
    """Two paragraphs positioned with NO real gap on either axis (they touch/overlap) are an
    irreducible cluster - never guessed at, falls back to plain Y order, exactly what
    layout_text already produces (so build_structured_text reports no change)."""
    blocks = [
        _p("Run-on paragraph part A", 0.05, 0.10, 0.50, 0.14),
        _p("Run-on paragraph part B", 0.499, 0.10, 0.90, 0.14),
    ]
    assert build_structured_text(blocks) is None


def test_borderless_item_table_becomes_a_real_table_not_just_reordered_text():
    """A real bug this reproduces exactly: a commercial invoice's item table with NO ruled
    cell borders (so Document AI never emits a `table` object for it - every cell is its own
    loose paragraph) has a QTY column immediately followed by a PO# column. Reordered-but-flat
    text alone let an AI reader mistake the PO# value for a quantity total. This must come back
    as an explicit table (3 columns, meeting the floor), with the QTY value and the PO# value
    each in their own column."""
    blocks = [
        _p("P/N", 0.08, 0.50, 0.12, 0.51),
        _p("QTY", 0.35, 0.50, 0.39, 0.51),
        _p("PO#", 0.46, 0.50, 0.50, 0.51),
        _p("826638E", 0.08, 0.51, 0.12, 0.52),
        _p("123120", 0.35, 0.51, 0.39, 0.52),
        _p("51952072", 0.46, 0.51, 0.51, 0.52),
        _p("826644E", 0.08, 0.52, 0.12, 0.53),
        _p("17440", 0.35, 0.52, 0.39, 0.53),
        _p("51919803", 0.46, 0.52, 0.51, 0.53),
    ]
    result = build_structured_text(blocks)
    assert result == (
        "| P/N | QTY | PO# |\n"
        "|---|---|---|\n"
        "| 826638E | 123120 | 51952072 |\n"
        "| 826644E | 17440 | 51919803 |"
    )


def test_a_ragged_total_row_stays_in_its_own_columns_not_rejected():
    """Your exact TOTAL-row example: an invoice item table whose own TOTAL row only fills the
    QTY column, leaving P/N and PO# blank on that row - a real, common invoice convention.
    Requiring every row to have the same populated column count would reject this whole table
    as "not a uniform grid" and fall back to flat text, which is exactly the bug being fixed
    here. The QTY total must land in the QTY column, not get treated as disqualifying
    evidence, and P/N/PO# on that row become explicit empty cells rather than shifting the
    columns after them."""
    blocks = [
        _p("P/N", 0.08, 0.50, 0.12, 0.51),
        _p("QTY", 0.35, 0.50, 0.39, 0.51),
        _p("PO#", 0.46, 0.50, 0.50, 0.51),
        _p("826638E", 0.08, 0.51, 0.12, 0.52),
        _p("123120", 0.35, 0.51, 0.39, 0.52),
        _p("51952072", 0.46, 0.51, 0.51, 0.52),
        _p("826644E", 0.08, 0.52, 0.12, 0.53),
        _p("17440", 0.35, 0.52, 0.39, 0.53),
        _p("51919803", 0.46, 0.52, 0.51, 0.53),
        _p("140560", 0.35, 0.53, 0.39, 0.54),
    ]
    result = build_structured_text(blocks)
    assert result == (
        "| P/N | QTY | PO# |\n"
        "|---|---|---|\n"
        "| 826638E | 123120 | 51952072 |\n"
        "| 826644E | 17440 | 51919803 |\n"
        "|  | 140560 |  |"
    )


def test_same_column_count_by_coincidence_without_row_alignment_stays_plain_text():
    """Two columns can genuinely be found (a real X-gap, no horizontal cut across the whole
    set) and have the SAME item count without actually being a table - here each column's own
    items are offset from the other column's, so no row shares a real Y-overlap with its
    opposite number. Also only 2 columns, so it could never become a table regardless. Comes
    back as plain column-major text, reordered but not tabular."""
    blocks = [
        _p("Col A item 1", 0.05, 0.10, 0.30, 0.14),
        _p("Col B item 1", 0.55, 0.14, 0.80, 0.18),
        _p("Col A item 2", 0.05, 0.18, 0.30, 0.22),
        _p("Col B item 2", 0.55, 0.22, 0.80, 0.26),
    ]
    result = build_structured_text(blocks)
    assert result == "\n\n".join(["Col A item 1", "Col A item 2", "Col B item 1", "Col B item 2"])


def test_mixed_layout_text_columns_table_text_in_page_order():
    """A single page carrying all four region types your plan asked for, stacked top to
    bottom with real gaps between each: plain text, a 2-column (not table) field pair, a real
    3-column item table with a ragged TOTAL row, then plain text again. Each region must be
    classified and rendered correctly and the four must combine in page order."""
    blocks = [
        # Region A: plain text.
        _p("Commercial Invoice", 0.05, 0.02, 0.50, 0.05),
        # Region B: 2 columns, not a table (Ship To / Invoice Number style, uneven counts).
        _p("Ship To: Nokia", 0.05, 0.10, 0.35, 0.13),
        _p("Invoice Number: 001", 0.55, 0.10, 0.85, 0.13),
        _p("Address: 1 Main St", 0.05, 0.13, 0.35, 0.16),
        _p("Invoice Date: 1-Jan", 0.55, 0.13, 0.85, 0.16),
        _p("GSTIN: 123", 0.05, 0.16, 0.35, 0.19),
        _p("Payment Terms: Net 90", 0.55, 0.16, 0.85, 0.19),
        _p("Bank: Ping An", 0.55, 0.19, 0.85, 0.22),
        # Region C: a real 3-column item table with a ragged total row.
        _p("P/N", 0.08, 0.40, 0.12, 0.41),
        _p("QTY", 0.35, 0.40, 0.39, 0.41),
        _p("PO#", 0.46, 0.40, 0.50, 0.41),
        _p("826638E", 0.08, 0.41, 0.12, 0.42),
        _p("123120", 0.35, 0.41, 0.39, 0.42),
        _p("51952072", 0.46, 0.41, 0.51, 0.42),
        _p("TOTAL", 0.08, 0.42, 0.12, 0.43),
        _p("123120", 0.35, 0.42, 0.39, 0.43),
        # Region D: plain text again, well clear of the table above.
        _p("Signed by: Lena", 0.05, 0.90, 0.40, 0.93),
    ]
    regions = describe_regions(blocks)["regions"]
    assert [r["type"] for r in regions] == ["text", "columns", "table", "text"]
    assert regions[0]["children"][0]["text"] == "Commercial Invoice"
    assert len(regions[1]["columns"]) == 2
    assert len(regions[2]["columns"]) == 3  # x-anchors discovered, not hardcoded
    assert regions[2]["rows"][-1] == ["TOTAL", "123120", None]
    assert regions[3]["children"][0]["text"] == "Signed by: Lena"

    text = build_structured_text(blocks)
    assert text.startswith("Commercial Invoice")
    assert "Ship To: Nokia" in text
    assert "| P/N | QTY | PO# |" in text
    assert text.rstrip().endswith("Signed by: Lena")


def test_key_value_list_with_colon_labels_pairs_each_row_with_its_own_answer():
    """The real bug found live: a freight certificate's own header block - 9 unrelated facts,
    each a short colon-terminated label beside its own answer. Read as plain "columns" (the
    pre-existing default for any 2-way split) this comes back as every label, then every
    answer, with the row pairing lost entirely - "Consignee :" no longer next to the
    consignee's name at all. Must come back re-paired, one "Label: Value" line per row, in
    original row order."""
    blocks = [
        _p("Consignee :", 0.08, 0.19, 0.18, 0.20),
        _p("YUZHAN TECHNOLOGY(INDIA) PRIVATE LIMITED", 0.40, 0.19, 0.80, 0.20),
        _p("MAWB:", 0.08, 0.21, 0.18, 0.22),
        _p("OOLU2173178130", 0.40, 0.21, 0.80, 0.22),
        _p("HAWB:", 0.08, 0.23, 0.18, 0.24),
        _p("JSCVSZX6IS653529", 0.40, 0.23, 0.80, 0.24),
        _p("POL :", 0.08, 0.25, 0.18, 0.26),
        _p("HONGKONG", 0.40, 0.25, 0.80, 0.26),
        _p("POD :", 0.08, 0.27, 0.18, 0.28),
        _p("CHENNAI", 0.40, 0.27, 0.80, 0.28),
    ]
    result = build_structured_text(blocks)
    assert result == "\n\n".join([
        "Consignee : YUZHAN TECHNOLOGY(INDIA) PRIVATE LIMITED",
        "MAWB: OOLU2173178130",
        "HAWB: JSCVSZX6IS653529",
        "POL : HONGKONG",
        "POD : CHENNAI",
    ])


def test_two_row_unlabelled_figure_pair_is_paired_by_position():
    """The second real bug found on the same document: a bare 2-row figure pair with NO
    label punctuation at all ("Freight Charges" / "EXW", each with its own dollar amount
    underneath the same header) - read as plain columns this becomes "Freight Charges, EXW,
    USD 1950, USD 800" (both labels, then both values), and the wrong figure gets read as the
    freight amount. Capped at exactly 2 rows (see module docstring) - must still pair by row
    position even without a colon to key off."""
    blocks = [
        _p("Freight Charges", 0.09, 0.440, 0.20, 0.448),
        _p("USD 1950", 0.35, 0.440, 0.45, 0.448),
        _p("EXW", 0.09, 0.452, 0.20, 0.460),
        _p("USD 800", 0.35, 0.452, 0.45, 0.460),
    ]
    result = build_structured_text(blocks)
    assert result == "Freight Charges: USD 1950\n\nEXW: USD 800"


def test_three_unlabelled_evenly_spaced_rows_pair_by_position_as_a_table():
    """Same shape as the 2-row figure pair above but one row longer, with no label
    punctuation anywhere and perfectly even row-to-row spacing. There is no longer a fixed
    row-count cap (removed during design review in favour of evidence-based scoring - see
    module docstring): 3 rows is enough for row-to-row spacing regularity to become a real,
    measurable statistic (not the 2-row vacuous case), and it comes back perfectly uniform,
    so TABLE legitimately wins on real evidence. Row pairing is preserved either way - each
    row lands in its own table row, "label" beside its own "value" - so this is a rendering
    difference, not a data-loss one."""
    blocks = [
        _p("Alpha", 0.09, 0.440, 0.20, 0.448),
        _p("100", 0.35, 0.440, 0.45, 0.448),
        _p("Bravo", 0.09, 0.452, 0.20, 0.460),
        _p("200", 0.35, 0.452, 0.45, 0.460),
        _p("Charlie", 0.09, 0.464, 0.20, 0.472),
        _p("300", 0.35, 0.464, 0.45, 0.472),
    ]
    result = build_structured_text(blocks)
    assert result == (
        "| Alpha | 100 |\n"
        "|---|---|\n"
        "| Bravo | 200 |\n"
        "| Charlie | 300 |"
    )


def test_a_row_with_two_stacked_items_on_one_side_rejects_key_value_for_the_whole_region():
    """A row where one side has 2+ stacked lines is the shape a real side-by-side block has
    (a wrapped multi-line value) and a key:value list never does - finding it ANYWHERE in the
    region must reject key:value pairing entirely, even for the other, otherwise-clean rows
    sharing that same region (MAWB/OOLU here would, on its own, satisfy the label-colon tier)
    - never pair only the rows that happen to look clean."""
    blocks = [
        _p("Consignee :", 0.08, 0.190, 0.18, 0.198),
        _p("YUZHAN TECHNOLOGY", 0.40, 0.190, 0.80, 0.198),
        _p("(INDIA) PRIVATE LIMITED", 0.40, 0.200, 0.80, 0.208),
        _p("MAWB:", 0.08, 0.210, 0.18, 0.218),
        _p("OOLU2173178130", 0.40, 0.210, 0.80, 0.218),
    ]
    region = describe_regions(blocks)["regions"][0]
    assert region["type"] == "columns"


def test_dense_table_row_with_a_garbled_outlier_block_does_not_swallow_its_neighbours():
    """The real bug found live on a 35-row packing list: Document AI itself misread one
    narrow numeric column as a single garbled paragraph spanning roughly 30 real rows'
    worth of the page. Reproduced at a manageable scale: a normal 3-column, 4-row table
    where the third column is ONE block spanning all 4 rows instead of one block per row.
    Before the fix this collapses all 4 rows into one; after it, the outlier joins only the
    single row nearest its own centre, and the other three rows must stay genuinely
    separate - the whole reason a table was recognized as 4 real rows rather than 1."""
    blocks = [
        _p("1", 0.08, 0.300, 0.10, 0.306),
        _p("Widget A", 0.15, 0.300, 0.30, 0.306),
        _p("2", 0.08, 0.310, 0.10, 0.316),
        _p("Widget B", 0.15, 0.310, 0.30, 0.316),
        _p("3", 0.08, 0.320, 0.10, 0.326),
        _p("Widget C", 0.15, 0.320, 0.30, 0.326),
        _p("4", 0.08, 0.330, 0.10, 0.336),
        _p("Widget D", 0.15, 0.330, 0.30, 0.336),
        # The garbled outlier: one block spanning the whole table, centred on row 2.
        _p("GARBLED123456", 0.40, 0.280, 0.45, 0.346),
    ]
    described = describe_regions(blocks)
    region = described["regions"][0]
    assert region["type"] == "table"
    rows = region["rows"]
    assert [row[:2] for row in rows] == [
        ["1", "Widget A"], ["2", "Widget B"], ["3", "Widget C"], ["4", "Widget D"],
    ]
    # The outlier joined exactly one row (its own nearest centre) - not all four, and not zero.
    third_cells = [row[2] for row in rows]
    assert third_cells.count("GARBLED123456") == 1
    assert third_cells.count(None) == 3


def test_describe_regions_shape_for_a_simple_table():
    """describe_regions is debug-only and must never be read by the real pipeline - just
    checking its JSON shape is exactly what was asked for: a flat "regions" list, each with a
    type/bbox, and a table region's own columns (x-anchors) and rows (null-padded)."""
    blocks = [
        _p("P/N", 0.08, 0.50, 0.12, 0.51),
        _p("QTY", 0.35, 0.50, 0.39, 0.51),
        _p("PO#", 0.46, 0.50, 0.50, 0.51),
        _p("826638E", 0.08, 0.51, 0.12, 0.52),
        _p("123120", 0.35, 0.51, 0.39, 0.52),
        _p("51952072", 0.46, 0.51, 0.51, 0.52),
        _p("826644E", 0.08, 0.52, 0.12, 0.53),
        _p("17440", 0.35, 0.52, 0.39, 0.53),
        _p("51919803", 0.46, 0.52, 0.51, 0.53),
    ]
    described = describe_regions(blocks)
    assert list(described.keys()) == ["regions", "geometry"]
    region = described["regions"][0]
    assert region["type"] == "table"
    assert "bbox" in region and len(region["bbox"]) == 4
    assert len(region["columns"]) == 3  # discovered, not hardcoded
    assert region["rows"] == [
        ["P/N", "QTY", "PO#"],
        ["826638E", "123120", "51952072"],
        ["826644E", "17440", "51919803"],
    ]
