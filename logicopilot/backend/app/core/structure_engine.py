"""A lightweight, optional reading-order reconstruction step for Document AI's own detected
paragraphs - pure Python geometry, no OCR, no ML/VLM/LLM, no new dependency. Sits between
docai.py's OCR call and the existing extraction pipeline (extraction.py) exactly the way
_page_layout_text already does; this module is a smarter REPLACEMENT for that function's
"sort everything by Y" step, not a new pipeline stage of its own.

WHY THIS EXISTS: _page_layout_text sorts every paragraph on a page purely by vertical
position. That is right for an ordinary single-column document, but a real invoice with a
genuine multi-column header (Seller printed beside Ship From, with a second, unrelated
Payment-Terms/Order-Type pair sharing the very same rows) gets its columns interleaved into
one scrambled line - this caused a real bug where a Supplier Address field spliced together
two different companies' addresses. A second real bug (a borderless item table's QTY value
read into a total_quantity field, PO# read into a material code) needed the same geometry
taken one step further: an explicit table structure, not just better-ordered text.

TWO EARLIER ATTEMPTS were tried and abandoned before this one:
1. A fixed left-edge X-gap threshold applied globally - fired on ordinary single-column pages
   too (list indentation, centered captions, right-aligned numbers all produce "different X"
   without being genuine columns).
2. A DocVortex-inspired row-bucketing + coverage-thresholded column detector (grouping rows
   that already have 2+ items, requiring a column to recur in >=50% of them). This correctly
   avoided the first attempt's false positives, but failed differently on live testing: it
   treated every row in a whole multi-row RUN as one column-detection problem, so a run
   spanning the Seller/Ship-From block AND an unrelated Payment-Terms/Order-Type row (sharing
   no real column alignment with the address block, but close enough in Y to join the same
   run) got column-sorted TOGETHER - producing an even more scrambled address than doing
   nothing at all.

THIS VERSION is XY-Cut (Ha, Haralick & Phillips, 1995), the standard document-layout-analysis
algorithm for reading order, reimplemented here from scratch in plain Python (no code, model,
or dependency taken from any project), now organized as two explicit passes rather than one:

1. REGION DETECTION (`_segment_regions`) - a recursive projection-profile split that produces
   a TREE of typed regions ("text" | "columns" | "table"), and decides nothing about final
   reading order yet:
   a. First look for a HORIZONTAL cut - a Y-range no block's vertical span crosses - splitting
      the page into top/bottom bands, recursing into each independently and concatenating
      their own region lists in order. This is tried BEFORE any column logic, and it is what
      keeps an ordinary page an exact no-op: consecutive paragraphs almost always have a real
      Y-gap between them, so recursion isolates every paragraph into its own "text" region
      before column or table logic is ever reached.
   b. Only once a set of blocks has NO horizontal gap - meaning they genuinely share one or
      more visual rows - does it look for a VERTICAL cut (an X-range no block's horizontal
      span crosses). Finding one means these are independent side-by-side regions - a
      "columns" classification - regardless of how many rows land in each column; row COUNT
      was never part of this test and still isn't.
   c. A "columns" split is further promoted to "table" only when it also shows genuine
      REPEATED row/column structure (see _table_grid) - never merely because it has 2+ rows.
      A region is only ever split against blocks INSIDE that same region - the Payment-Terms/
      Order-Type row and the Seller/Ship-From address lines are never forced into one shared
      column-detection pass unless a real, page-geometry-verified gap actually separates them
      from everything else at some recursion level.
   d. A leaf (no further cut found either way) becomes a "text" region in plain top-to-bottom
      order - the same fallback plain sorting already produces, so nothing is ever worse than
      doing nothing.
2. READING ORDER (`_reading_order`) - a separate pass that walks the region tree and produces
   the final ordered sequence: a "text" region contributes its own blocks; a "columns" region
   contributes each column's own reading order, left to right; a "table" region contributes
   ONE synthetic block holding a rendered Markdown table.

Tables Document AI ITSELF already detected (ruled borders) are passed in as single atomic
blocks (their own cell text_anchors already keep columns correct - see docai.py's
_table_markdown) and are never split internally by this algorithm, exactly as
_page_layout_text already treats them.

A BORDERLESS table - very common on a commercial invoice's own item table, printed with
whitespace alignment only - is invisible to Document AI's table detector, so it comes in here
as loose paragraphs, one per cell, with no special status at all. A "columns" region is
promoted to "table" only when ALL of the following hold - COLUMN COUNT vs. ROW COUNT are
deliberately different tests, because a 2-column split (Seller printed beside Ship From, a
form field pattern) must never become a table no matter how cleanly its rows align, while a
real item table (P/N, Description, QTY, PO#, ...) must be recognized even though its own
TOTAL row rarely populates every column:
  - at least _MIN_TABLE_COLUMNS (3) columns were discovered - a pure 2-way split is always a
    "columns" region, never a table, regardless of row alignment;
  - at least _MIN_TABLE_ROWS (2) distinct row-bands exist (by real Y-overlap, not just the
    touching-counts-as-merged rule the cut-finder itself uses);
  - at least _MIN_TABLE_ROWS of those row-bands populate 2+ of the discovered columns;
  - one shared column-anchor set (found once, from every block in the whole region together)
    is reused for every row's own cell assignment, rather than re-detecting columns per row.
None of these thresholds cap how many columns or rows can be discovered - a table can have 3
columns or 30, 2 rows or 500; the numbers above are confidence gates on trusting the
classification, not a fixed table size. A row missing a value in some column (a real invoice's
own total/summary row almost never fills every column) gets an empty cell there rather than
having its values shifted into the wrong column, exactly the tolerance _table_markdown's own
ragged-row handling already has for a genuine Document AI table.
"""

from __future__ import annotations

# Minimum normalized (0..1) gap required to trust a horizontal (Y) or vertical (X) split -
# small enough to catch a real paragraph-to-paragraph gap or column gutter, large enough to
# ignore OCR bounding-box jitter of a pixel or two between blocks that are not really split.
_MIN_Y_GAP = 0.010
_MIN_X_GAP = 0.02

# Two blocks are judged to share a visual row when their vertical spans overlap by at least
# this fraction of the shorter block's own height - the same test _table_row_count's own
# row-matching logic and the earlier row-bucketing attempt both used.
_ROW_OVERLAP_RATIO = 0.4
# A pair of qualifying rows was tried first and found insufficient by live regression testing
# against real documents: a lone coincidental pair (an unrelated sentence sitting above a real
# table's own header row, say) can each independently look like a populated row of the same
# broad column split. 3 is the smallest floor that held up against every real false positive
# found without rejecting any genuine table tried - not a cap on how many rows a table can
# have, which stays fully open above this floor.
_MIN_TABLE_ROWS = 3
# A pure 2-way split is a form/field pattern (Seller beside Ship From), never a table, no
# matter how many rows align - this is what keeps that shape a "columns" region permanently.
# A real data table (P/N, Description, UNIT, QTY, PO#, ...) has multiple DISTINCT fields per
# record, which is why 3 is the right floor: it's the smallest column count that can't also be
# read as "two parallel blocks of prose printed side by side".
_MIN_TABLE_COLUMNS = 3


def _bbox_of(blocks: list[dict]) -> list[float]:
    if not blocks:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        min(b["x0"] for b in blocks),
        min(b["y0"] for b in blocks),
        max(b["x1"] for b in blocks),
        max(b["y1"] for b in blocks),
    ]


def _row_groups(blocks: list[dict]) -> list[list[dict]]:
    """blocks: everything in one confirmed vertical-cut region, across every column. Buckets
    them into visual rows by real Y-overlap (the same test _table_row_count's own row matching
    already uses) - NOT the touching-counts-as-merged rule _cut_points uses for deciding
    whether to cut at all; a table's own rows must share a genuine overlap, not just abut."""
    rows: list[dict] = []
    for b in sorted(blocks, key=lambda b: b["y0"]):
        height = max(b["y1"] - b["y0"], 1e-6)
        placed = None
        for row in rows:
            row_height = max(row["y1"] - row["y0"], 1e-6)
            overlap = min(b["y1"], row["y1"]) - max(b["y0"], row["y0"])
            if overlap / min(height, row_height) >= _ROW_OVERLAP_RATIO:
                placed = row
                break
        if placed is None:
            rows.append({"y0": b["y0"], "y1": b["y1"], "items": [b]})
        else:
            placed["items"].append(b)
            placed["y0"] = min(placed["y0"], b["y0"])
            placed["y1"] = max(placed["y1"], b["y1"])
    return [r["items"] for r in rows]


def _table_grid(columns: list[list[dict]]) -> list[list[dict | None]] | None:
    """columns: the X-groups from one XY-Cut vertical split - already confirmed to be genuine,
    gap-separated columns sharing this region, AND already confirmed to number at least
    _MIN_TABLE_COLUMNS (the caller's job, not this function's - see the module docstring for
    why column count and row count are two separate tests). Builds a RAGGED grid: every block
    gets bucketed into a row by real Y-overlap, then assigned to its nearest column by
    X-position - a row missing a value in some column (a summary/total row is the common real
    case: only the QTY and Amount columns are filled) gets an empty cell there, exactly the
    tolerance _table_markdown's own ragged-row handling already has, rather than an
    all-or-nothing per-column count match (which real invoices routinely fail on their own
    total row).

    Rejected (returns None) unless BOTH hold - two real false positives found in live
    regression testing, each needing its own test:
    1. At least _MIN_TABLE_ROWS rows genuinely have 2+ populated columns. A bare pair of
       unrelated rows (an AWB's own boilerplate sentence sitting a real gap above that same
       AWB's genuine rate-table header row, say) can each independently have 2+ populated
       columns by sheer coincidence of X position - _MIN_TABLE_ROWS is 3, not 2, because two
       rows alone were never enough to tell a real repeating grid apart from that coincidence
       in practice, even though the row/column COUNTS a table can have are still fully
       open-ended above that floor.
    2. The columns actually populated are the SAME across every qualifying row - not merely
       each row having "some 2 columns or other". A commercial invoice's unrelated footer
       lines (a sub-total on the right, a net-weight figure on the left, printed one above the
       other with no real gap between them) can each look like a populated row of the same
       broad column split while sharing NO column in common with each other at all - which is
       exactly what a genuine table's own columns never do; a real column keeps its meaning
       down every row. Checked as a non-empty intersection of populated-column-indices across
       every qualifying row - this is deliberately strict (ANY qualifying row missing from the
       common set fails the whole region), because a table needs its columns to mean the same
       thing in literally every row that claims to belong to it."""
    means = [sum((b["x0"] + b["x1"]) / 2 for b in c) / len(c) for c in columns]
    rows = _row_groups([b for c in columns for b in c])
    grid: list[list[dict | None]] = []
    common_columns: set[int] | None = None
    multi_item_rows = 0
    for items in rows:
        cells: list[dict | None] = [None] * len(means)
        for b in items:
            anchor = (b["x0"] + b["x1"]) / 2
            nearest = min(range(len(means)), key=lambda i: abs(anchor - means[i]))
            if cells[nearest] is None:
                cells[nearest] = b
            else:
                # Two fragments landing in the same cell - a wrapped multi-line value split
                # into separate paragraphs by Document AI. Merge rather than silently drop one.
                cells[nearest] = {**cells[nearest], "text": cells[nearest]["text"] + " " + b["text"]}
        grid.append(cells)
        populated = {i for i, c in enumerate(cells) if c is not None}
        if len(populated) >= 2:
            multi_item_rows += 1
            common_columns = populated if common_columns is None else (common_columns & populated)
    if multi_item_rows < _MIN_TABLE_ROWS or not common_columns:
        return None
    return grid


def _table_block(grid: list[list[dict | None]]) -> dict:
    """grid: ragged, ~from _table_grid or a table region's own "rows" - each row the same
    width, empty cells as None. Builds one synthetic block whose text is a Markdown table
    (first row as header, same convention _table_markdown falls back to when Document AI
    declared no header row of its own) - and whose bbox spans every populated cell, so it
    behaves as one atomic unit to any recursion around it, exactly like a real Document AI
    table already does."""
    def cell_text(b: dict | None) -> str:
        return b["text"].replace("\n", " ").strip() if b else ""

    width = len(grid[0])
    header = [cell_text(c) for c in grid[0]]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * width) + "|"]
    for row in grid[1:]:
        lines.append("| " + " | ".join(cell_text(c) for c in row) + " |")
    all_blocks = [b for row in grid for b in row if b is not None]
    return {**_bbox_dict(all_blocks), "kind": "table", "text": "\n".join(lines)}


def _bbox_dict(blocks: list[dict]) -> dict:
    x0, y0, x1, y1 = _bbox_of(blocks)
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


def _merged_coverage(spans: list[tuple[float, float]]) -> list[list[float]]:
    """spans: (start, end) intervals, any order. Returns the merged (non-overlapping, sorted)
    coverage intervals - the gaps BETWEEN these are the only candidate cut points, since a cut
    point inside any single span would cut through that block."""
    merged: list[list[float]] = []
    for s, e in sorted(spans):
        if merged and s <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], e)
        else:
            merged.append([s, e])
    return merged


def _cut_points(spans: list[tuple[float, float]], min_gap: float) -> list[float]:
    merged = _merged_coverage(spans)
    return [
        (merged[i][1] + merged[i + 1][0]) / 2.0
        for i in range(len(merged) - 1)
        if merged[i + 1][0] - merged[i][1] >= min_gap
    ]


def _split_at(blocks: list[dict], cuts: list[float], axis: str) -> list[list[dict]] | None:
    if not cuts:
        return None
    groups: list[list[dict]] = [[] for _ in range(len(cuts) + 1)]
    for b in blocks:
        lo, hi = (b["x0"], b["x1"]) if axis == "x" else (b["y0"], b["y1"])
        mid = (lo + hi) / 2.0
        idx = sum(1 for c in cuts if mid > c)
        groups[idx].append(b)
    groups = [g for g in groups if g]
    # A cut point sits strictly between two merged coverage intervals, so every block's
    # midpoint falls unambiguously on one side or the other - this is a sanity check, not a
    # real-world case, but a degenerate split (everything landed in one group) must never be
    # treated as progress, or recursion would loop forever on the same set.
    return groups if len(groups) >= 2 else None


def _column_means(blocks: list[dict]) -> list[float] | None:
    """If this exact set of blocks has a genuine vertical split of its own, returns its sorted
    column X-anchors (means); None if no split is found (a single paragraph, or an irreducible
    cluster). Used only to test whether two ADJACENT horizontal bands are really one table torn
    apart by a real-but-ordinary row-to-row gap - see _merge_table_bands."""
    x_groups = _split_at(blocks, _cut_points([(b["x0"], b["x1"]) for b in blocks], _MIN_X_GAP), "x")
    if x_groups is None:
        return None
    return sorted(sum((b["x0"] + b["x1"]) / 2 for b in c) / len(c) for c in x_groups)


def _anchors_match(a: list[float], b: list[float]) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= _MIN_X_GAP / 2 for x, y in zip(a, b))


def _table_region_from(blocks: list[dict]) -> dict | None:
    x_groups = _split_at(blocks, _cut_points([(b["x0"], b["x1"]) for b in blocks], _MIN_X_GAP), "x")
    if x_groups is None or len(x_groups) < _MIN_TABLE_COLUMNS:
        return None
    grid = _table_grid(x_groups)
    if grid is None:
        return None
    means = [sum((b["x0"] + b["x1"]) / 2 for b in c) / len(c) for c in x_groups]
    populated = [b for row in grid for b in row if b is not None]
    return {"type": "table", "bbox": _bbox_of(populated), "columns": means, "rows": grid}


def _merge_table_bands(y_groups: list[list[dict]]) -> list[dict]:
    """A genuine table's own rows can have a real, ordinary row-to-row gap slightly larger
    than _MIN_Y_GAP (ordinary body-text paragraph spacing, not a bridging fluke) - the row-first
    horizontal split then tears it into several single/few-row bands, each with too little
    evidence on its own to pass the table test, even though every one of them shares the exact
    same column positions. This looks ACROSS already-separated sibling bands (never across
    unrelated content further away - only strictly adjacent ones) for a run that shares the
    same column anchors, and retries the table test on their UNION before giving up on each
    band individually. Returns the final, page-ordered region list."""
    out: list[dict] = []
    i = 0
    while i < len(y_groups):
        anchors = _column_means(y_groups[i])
        run = [y_groups[i]]
        j = i + 1
        while anchors is not None and j < len(y_groups):
            next_anchors = _column_means(y_groups[j])
            if next_anchors is None or not _anchors_match(anchors, next_anchors):
                break
            run.append(y_groups[j])
            j += 1
        if len(run) >= 2:
            merged = _table_region_from([b for g in run for b in g])
            if merged is not None:
                out.append(merged)
                i = j
                continue
        # No compatible run, or the merged run still didn't pass the table test - fall back to
        # each band's own, already-verified independent handling. Only ONE band is consumed
        # here (not the whole failed run), so the next iteration re-tries matching fresh from
        # the very next band rather than giving up on the rest of the run too.
        out.extend(_segment_regions(y_groups[i]))
        i += 1
    return out


def _segment_regions(blocks: list[dict]) -> list[dict]:
    """REGION DETECTION (pass 1 of 2 - see module docstring). Returns a flat, page-ordered
    list of typed region nodes:
      {"type": "text", "bbox": [...], "children": [block, ...]}
      {"type": "columns", "bbox": [...], "columns": [[Region, ...], [Region, ...], ...]}
      {"type": "table", "bbox": [...], "columns": [x_anchor, ...], "rows": [[block|None, ...], ...]}
    Says nothing yet about how a "columns" region's own columns get concatenated - that is
    _reading_order's job, entirely separately."""
    if len(blocks) <= 1:
        return [{"type": "text", "bbox": _bbox_of(blocks), "children": list(blocks)}]

    y_groups = _split_at(blocks, _cut_points([(b["y0"], b["y1"]) for b in blocks], _MIN_Y_GAP), "y")
    if y_groups is not None:
        return _merge_table_bands(y_groups)

    x_groups = _split_at(blocks, _cut_points([(b["x0"], b["x1"]) for b in blocks], _MIN_X_GAP), "x")
    if x_groups is not None:
        table_region = _table_region_from(blocks)
        if table_region is not None:
            return [table_region]
        return [{
            "type": "columns",
            "bbox": _bbox_of(blocks),
            "columns": [_segment_regions(g) for g in x_groups],  # left-to-right, ascending
        }]

    # Neither axis found a genuine gap - an irreducible cluster. Keep plain Y order rather
    # than guess; this is exactly what today's layout_text would already have produced.
    return [{"type": "text", "bbox": _bbox_of(blocks), "children": sorted(blocks, key=lambda b: b["y0"])}]


def _reading_order(regions: list[dict]) -> list[dict]:
    """READING ORDER (pass 2 of 2 - see module docstring). Walks a region tree from
    _segment_regions and produces the final flat, ordered sequence of renderable blocks - a
    "text" region contributes its own blocks, a "columns" region contributes each column's own
    reading order in turn (left to right), a "table" region contributes ONE synthetic block
    holding the rendered Markdown table."""
    out: list[dict] = []
    for region in regions:
        kind = region["type"]
        if kind == "text":
            out.extend(region["children"])
        elif kind == "columns":
            for column_regions in region["columns"]:
                out.extend(_reading_order(column_regions))
        elif kind == "table":
            out.append(_table_block(region["rows"]))
    return out


def build_structured_text(blocks: list[dict]) -> str | None:
    """blocks: one page's worth of Document AI paragraphs AND tables, already rendered/typed
    by the caller (docai.py) - each a dict with at least {"kind": "paragraph" | "table",
    "x0", "y0", "x1", "y1", "text"}.

    Returns the joined string in the reconstructed reading order, or None when the result came
    out identical to plain top-to-bottom order - the caller falls back to the existing
    layout_text untouched in that case (also the outcome of any internal error), so a page this
    engine cannot help, or gets wrong, is guaranteed to read exactly as it always has.
    """
    try:
        if len(blocks) < 2:
            return None
        tagged = [dict(b, id=i) for i, b in enumerate(blocks)]
        plain_order = sorted(tagged, key=lambda b: b["y0"])
        regions = _segment_regions(list(plain_order))
        ordered = _reading_order(regions)
        # A synthetic table block (_table_block) carries no "id" - it replaces several
        # original blocks with one, so .get() rather than a bare [] lookup is required here;
        # it can never equal plain_order's own ids either way, which is correct - collapsing
        # several blocks into one table is always a change worth returning.
        if [b.get("id") for b in ordered] == [b["id"] for b in plain_order]:
            return None
        return "\n\n".join(b["text"] for b in ordered) or None
    except Exception:  # noqa: BLE001 — structure is a bonus, never a hard dependency
        return None


def describe_regions(blocks: list[dict]) -> dict:
    """DEBUG-ONLY structural representation of one page - never consumed by the real
    extraction pipeline and never changes the business JSON. Wired only into field_marks.py's
    own debug=true test-extract path, for verifying/validating the region tree directly."""
    try:
        tagged = [dict(b, id=i) for i, b in enumerate(blocks)]
        plain_order = sorted(tagged, key=lambda b: b["y0"])
        regions = _segment_regions(list(plain_order))
        return {"regions": [_region_to_json(r) for r in regions]}
    except Exception:  # noqa: BLE001
        return {"regions": []}


def _region_to_json(region: dict) -> dict:
    kind = region["type"]
    if kind == "text":
        return {
            "type": "text",
            "bbox": region["bbox"],
            "children": [
                {"bbox": [b["x0"], b["y0"], b["x1"], b["y1"]], "text": b["text"]}
                for b in region["children"]
            ],
        }
    if kind == "columns":
        return {
            "type": "columns",
            "bbox": region["bbox"],
            "columns": [[_region_to_json(r) for r in col] for col in region["columns"]],
        }
    if kind == "table":
        return {
            "type": "table",
            "bbox": region["bbox"],
            "columns": region["columns"],
            "rows": [[(c["text"] if c else None) for c in row] for row in region["rows"]],
        }
    return region
