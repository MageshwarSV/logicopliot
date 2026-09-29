"""build_structured_text / describe_regions (app/core/structure_engine.py) - the two-pass
region-detection-then-reading-order engine sitting between docai.py's flat paragraph/table
geometry and the existing layout_text no-op fallback. Every case here is built from plain
dicts (the exact contract docai.py's _page_blocks hands the engine), not Document AI proto
objects.

The central distinction under test throughout: COLUMN DETECTION ("are these two spatially
independent regions?") and TABLE DETECTION ("does this region show genuine repeated row/column
structure?") are two separate questions. A region only ever gets promoted from "columns" to
"table" when it has at least 3 discovered columns AND real row-band recurrence - a pure
2-column split (the classic Seller/Ship-From form-field pattern) must NEVER become a table no
matter how cleanly its rows align, while a real item table (3+ distinct fields per record)
must be recognized even though its own TOTAL row rarely populates every column."""
from app.core.structure_engine import build_structured_text, describe_regions


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


def test_genuine_two_column_header_stays_columns_not_table():
    """A real Seller/Ship-From style header: two columns, 4 rows tall, with NO gap anywhere
    between consecutive rows and a real X-gap between the two sides - every row's two items
    genuinely share one visual line, exactly the shape that would satisfy a naive "2+ rows,
    2+ populated columns" table test. It must NOT become a table: only 2 columns were
    discovered, and a 2-way split is always a form/columns region. Output stays column-major
    flat text - column A's own paragraphs top-to-bottom in full, then column B's."""
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
    assert result == "\n\n".join(
        [
            "Seller: ACME Corp",
            "123 Foo Ave",
            "Springfield, IL",
            "USA",
            "Ship From: 123 Main St",
            "456 Bar Blvd",
            "Chicago, IL",
            "USA",
        ]
    )


def test_table_block_stays_in_place_around_a_columns_header():
    """A real Document AI table sitting after a genuine two-column (not table) header keeps
    its own original slot - it has a clear Y-gap from the header above it, so the row-first
    split isolates it into its own later region, never pulled into the header's column
    analysis, and never split apart itself either."""
    blocks = [
        _p("Seller: ACME Corp", 0.05, 0.10, 0.35, 0.14),
        _p("Ship From: 123 Main St", 0.55, 0.10, 0.85, 0.14),
        _p("123 Foo Ave", 0.05, 0.14, 0.35, 0.18),
        _p("456 Bar Blvd", 0.55, 0.14, 0.85, 0.18),
        _p("Springfield, IL", 0.05, 0.18, 0.35, 0.22),
        _p("Chicago, IL", 0.55, 0.18, 0.85, 0.22),
        _t("| Item | Qty |\n| Widget | 10 |", 0.05, 0.30, 0.85, 0.45),
    ]
    result = build_structured_text(blocks)
    assert result == "\n\n".join(
        [
            "Seller: ACME Corp",
            "123 Foo Ave",
            "Springfield, IL",
            "Ship From: 123 Main St",
            "456 Bar Blvd",
            "Chicago, IL",
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
    assert list(described.keys()) == ["regions"]
    region = described["regions"][0]
    assert region["type"] == "table"
    assert "bbox" in region and len(region["bbox"]) == 4
    assert len(region["columns"]) == 3  # discovered, not hardcoded
    assert region["rows"] == [
        ["P/N", "QTY", "PO#"],
        ["826638E", "123120", "51952072"],
        ["826644E", "17440", "51919803"],
    ]
