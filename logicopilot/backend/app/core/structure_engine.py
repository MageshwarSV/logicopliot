"""A lightweight, optional reading-order reconstruction step for Document AI's own detected
paragraphs - pure Python geometry and statistics, no OCR, no ML/VLM/LLM, no new dependency
(only the standard library's own `statistics` module). Sits between docai.py's OCR call and
the existing extraction pipeline (extraction.py) exactly the way _page_layout_text already
does; this module is a smarter REPLACEMENT for that function's "sort everything by Y" step,
not a new pipeline stage of its own.

WHY THIS EXISTS: _page_layout_text sorts every paragraph on a page purely by vertical
position. That is right for an ordinary single-column document, but real documents break
this in two distinct ways, both found live and both fixed here:
1. A genuine multi-column header (Seller printed beside Ship From) gets its columns
   interleaved into one scrambled line if just read top-to-bottom - this caused a real bug
   where a Supplier Address field spliced together two different companies' addresses.
   A borderless item table needs the same geometry taken one step further: an explicit
   table structure, not just better-ordered text (a QTY value was once read into a
   total_quantity field, a PO# into a material code).
2. A short label:value list (a freight certificate's "Consignee :" / "MAWB:" / ... block, or
   a bare unlabelled 2-row figure pair like "Freight Charges" / "EXW") read as EVERY label
   then EVERY value under the same "read column, then column" rule that correctly protects
   case 1 above - re-pairing every value to the wrong label. Telling these two shapes apart,
   and a third (a genuine repeating-record table, of any column count) from both, is what
   most of this module's logic actually does.

THREE EARLIER ATTEMPTS at columns/tables were tried and abandoned before the current design:
1. A fixed left-edge X-gap threshold applied globally - fired on ordinary single-column pages
   too (list indentation, centered captions, right-aligned numbers all produce "different X"
   without being genuine columns).
2. A DocVortex-inspired row-bucketing + coverage-thresholded column detector - correctly
   avoided attempt 1's false positives, but treated every row in a whole multi-row run as one
   column-detection problem, so an address block and an unrelated same-Y row got column-
   sorted together, scrambling things worse than doing nothing.
3. A fixed-threshold table/key-value classifier (_MIN_TABLE_COLUMNS=3, a flat 0.6 confidence
   cutoff, a 2-row cap on unlabelled key:value, Wilson score intervals applied to continuous
   per-row evidence they were never designed for). Every one of these numbers was either
   fit to the two real documents on hand rather than derived from the page itself, or
   mathematically the wrong tool for a continuous measurement - both are exactly the shape
   of bug this module exists to avoid in the FIRST place. Replaced by the adaptive,
   evidence-comparing design below after live review.

CURRENT DESIGN - XY-Cut (Ha, Haralick & Phillips, 1995) for region detection, reimplemented
here from scratch in plain Python (no code, model, or dependency taken from any project),
plus an adaptive, statistics-based structural classifier layered on top:

1. ADAPTIVE GEOMETRY (`_page_geometry`) - computed ONCE per page, before any region
   detection: the page's own median block height, and its own typical row-gap and
   column-gap sizes, derived from the actual gaps present between this page's own blocks.
   Replaces what used to be two fixed constants (a 0.010 and a 0.02 page-fraction gap) with
   numbers that scale to THIS page's own font size and density - the packing-list bug (see
   below) happened on a page dense enough that the fixed 0.010 gap was uncomfortably close
   to real row-to-row spacing; an adaptive gap does not have that problem by construction.

2. REGION DETECTION (`_segment_regions`) - a recursive projection-profile split that
   produces a TREE of typed regions ("text" | "columns" | "table" | "key_value"):
   a. First look for a HORIZONTAL cut - a Y-range no block's vertical span crosses -
      splitting the page into top/bottom bands, recursing into each independently. This is
      tried BEFORE any column logic, and is what keeps an ordinary page an exact no-op:
      consecutive paragraphs almost always have a real Y-gap, so recursion isolates each
      into its own "text" region before column/table/key-value logic is ever reached.
   b. Only once a set of blocks shares one or more visual rows (no horizontal gap) does it
      look for a VERTICAL cut. Finding one means these are candidate side-by-side regions -
      by default "columns" (today's safe, pre-existing behaviour: read the left side fully,
      then the right) - but every such split is now scored against two competing structural
      hypotheses before settling for that default; see (c).
   c. STRUCTURAL HYPOTHESIS DETECTION (`_classify_column_region`) - TABLE (a genuine
      repeating-record grid, any column count 2 or more) and, for an exactly-2-way split
      only, KEY_VALUE (a row-by-row label:value pairing) are scored from the SAME
      underlying per-row evidence (does this row's populated columns match the pattern
      every other row shows; is the row-to-row spacing uniform, the way independent
      repeating records are typically spaced, rather than tightly clustered the way a
      wrapped multi-line entity's own lines are). Each hypothesis's score is turned into a
      one-sided confidence interval on its own mean (Student's t - the right tool for a
      small sample of CONTINUOUS scores; this module does NOT use Wilson/binomial
      intervals anywhere, precisely because that mismatch is what made an earlier version
      reject its own two-row Freight Charges/EXW case even at 100% agreement - see
      `_hypothesis_lower_bound`'s own docstring). A hypothesis is only accepted when its
      interval clears both a majority bar AND does not overlap the alternative's own
      interval - two close, statistically-indistinguishable readings fall back to plain
      "columns" rather than force a pick (see (d)). Column COUNT never gates entry into
      this test - a 2-column region can become a table given strong enough grid evidence,
      and a 7-column region does not become one automatically just from having 7 columns.
      REFINEMENT for an exactly-2-column region specifically: "every row has both its
      columns populated" is guaranteed by the column-assignment step itself whenever a row
      qualifies at all, so at 2 columns it carries ZERO discriminating evidence for TABLE
      (a genuine key:value pair satisfies it exactly as trivially as a genuine table does) -
      TABLE's only usable evidence there is row-to-row spacing regularity, which itself
      needs 3+ rows to even be measurable (see `_spacing_regularity`). Below that (a bare
      2-row region, the real Freight Charges/EXW case), TABLE simply has no assessable
      evidence and is not a candidate at all, so KEY_VALUE wins outright if it clears its
      own bar. At 3+ rows, when BOTH table and key_value clear their own bar on real,
      measured evidence, key_value is still preferred on overlap (rather than falling back
      to plain columns) as the more specific of the two hypotheses at exactly 2 columns -
      table only overrides it when table's own lower bound strictly exceeds key_value's own
      upper bound, i.e. the evidence genuinely, not just narrowly, favours table.
   d. Neither hypothesis clears its bar and separates from the other -> the pre-existing,
      already-tested "columns" behaviour, completely unchanged: read the left side's own
      reading order in full, then the right's. Incorrect structure is worse than none.
   e. A region is only ever split against blocks INSIDE that same region - an unrelated
      same-Y row is never forced into another region's own column-detection pass unless a
      real, page-geometry-verified gap actually separates them at some recursion level.
   f. A leaf (no cut found either way) becomes a "text" region in plain top-to-bottom
      order - the same fallback plain sorting already produces, so nothing here is ever
      worse than doing nothing.
3. HEIGHT-ANOMALY DETECTION (`_is_anomalous`, inside `_row_groups`) - a second real bug:
   on a packing list dense enough to pack 30+ product rows onto one page, Document AI itself
   misread one narrow numeric column as a single garbled paragraph roughly 30 rows tall
   ("88385777777-8823823333", not real text). The row-overlap test below is normalized by
   the SMALLER of two blocks' heights specifically so a real wrapped multi-line value still
   joins its own single row - but that same normalization let this outlier overlap, and
   silently absorb, every genuine row crossing its span. A block is now compared against
   several LOCAL, robust signals at once (height, relative to this region's own median via
   a MAD-based statistic; width/aspect ratio, against the same region's own distribution;
   Document AI's own per-paragraph confidence, when the OCR response carries one) and
   flagged only when a genuine MAJORITY of the signals actually available for it agree it is
   an outlier. A flagged block's TEXT and real bounding box are never touched - only a
   clamped stand-in span, centred on its own middle, is used for row-MATCHING purposes, so
   it still joins the one row it actually belongs to instead of swallowing its neighbours.
4. READING ORDER (`_reading_order`) - walks the region tree and produces the final ordered
   sequence: "text" contributes its own blocks; "columns" contributes each column's own
   reading order in turn, left to right; "table" contributes ONE synthetic block holding a
   rendered Markdown table; "key_value" contributes one "Label: Value" synthetic block per
   pair, in row order - re-attaching each label to its own answer instead of leaving every
   label on one side and every answer on the other.

Tables Document AI ITSELF already detected (ruled borders) are passed in as single atomic
blocks (their own cell text_anchors already keep columns correct - see docai.py's
_table_markdown) and are never split internally by this algorithm, exactly as
_page_layout_text already treats them. A BORDERLESS table - very common on a commercial
invoice's own item table, printed with whitespace alignment only - is invisible to Document
AI's table detector, so it comes in here as loose paragraphs, one per cell, with no special
status at all; that is exactly the shape (2) above reconstructs.

STATISTICAL METHOD, in one place: every "does the evidence support hypothesis X" question in
this module (TABLE vs. the implicit COLUMNS default, KEY_VALUE vs. COLUMNS) is answered by
`_hypothesis_lower_bound` - a one-sided Student's t confidence interval on the mean of
per-row continuous evidence scores. This single mechanism is what replaces every fixed
threshold an earlier version of this module used (a flat 3-column floor, a flat 0.6 cutoff,
a flat 2-row cap): more consistent evidence narrows the interval and raises confidence
regardless of how many rows are available; conflicting evidence widens it regardless of
count. It is NOT used for the height-anomaly check in (3) above - that is a continuous
per-BLOCK measurement problem answered with its own local median/MAD statistics instead,
deliberately kept as a separate mechanism rather than forced through the same tool.
"""

from __future__ import annotations

import statistics

# ---------------------------------------------------------------------------------------
# Adaptive geometry - replaces what used to be two fixed page-fraction constants. Computed
# once per page (see _page_geometry) from that page's OWN block heights and gaps, so the same
# logic below scales to a different font size, page size, or OCR resolution without a single
# document-specific number anywhere in this module.
# ---------------------------------------------------------------------------------------

# The one constant this module cannot derive from geometry: a page with too few blocks to
# compute ANY meaningful statistic from (a near-empty page). Never observed on a real
# document (the smallest seen so far had 32 blocks) - a safety floor for a degenerate input,
# not a policy choice about real documents.
_DEGENERATE_GAP = 0.01

# Two blocks are judged to share a visual row when their vertical spans overlap by at least
# this fraction of the shorter block's own height - MAJORITY overlap (more shared than not),
# a natural, non-arbitrary boundary rather than a tuned ratio.
_ROW_OVERLAP_RATIO = 0.5

# Standard external conventions, not fit to our documents - see the module docstring's
# "STATISTICAL METHOD" paragraph and _hypothesis_lower_bound / _is_anomalous below.
_MAD_TO_SD = 0.6745  # converts a median absolute deviation to an equivalent std-dev estimate
_HEIGHT_Z_CUTOFF = 3.5  # Iglewicz & Hoaglin's standard modified-z-score outlier convention
_T_CONFIDENCE_ONE_SIDED = 0.90  # the t-interval's confidence level throughout this module

# One-sided Student's t critical values at 90% confidence, by degrees of freedom (n-1) - a
# textbook table, not tuned to us. Falls back to the z=1.282 normal approximation above
# df=30 (the two agree to within 2% there, and converge exactly as df -> infinity).
_T_CRITICAL_90 = {
    1: 3.078, 2: 1.886, 3: 1.638, 4: 1.533, 5: 1.476, 6: 1.440, 7: 1.415, 8: 1.397, 9: 1.383,
    10: 1.372, 11: 1.363, 12: 1.356, 13: 1.350, 14: 1.345, 15: 1.341, 16: 1.337, 17: 1.333,
    18: 1.330, 19: 1.328, 20: 1.325, 21: 1.323, 22: 1.321, 23: 1.319, 24: 1.318, 25: 1.316,
    26: 1.315, 27: 1.314, 28: 1.313, 29: 1.311, 30: 1.310,
}
_Z_90 = 1.282


def _t_critical(df: int) -> float:
    if df in _T_CRITICAL_90:
        return _T_CRITICAL_90[df]
    return _Z_90 if df > 30 else _T_CRITICAL_90[1]


def _median(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _mad(values: list[float], center: float) -> float:
    """Median absolute deviation - a robust (outlier-resistant) alternative to standard
    deviation, used wherever this module needs "how spread out is this LOCAL distribution"
    without one extreme value distorting the answer - exactly the property a plain std-dev
    does not have and exactly why one is needed here at all."""
    return _median([abs(v - center) for v in values])


def _positive_gaps(spans: list[tuple[float, float]]) -> list[float]:
    """The real, positive gaps between this set of blocks' own merged coverage intervals -
    the page's own evidence for "how big is an ordinary gap here", used to derive the cut
    thresholds in _page_geometry instead of a fixed constant."""
    merged = _merged_coverage(spans)
    return [merged[i + 1][0] - merged[i][1] for i in range(len(merged) - 1) if merged[i + 1][0] > merged[i][1]]


def _page_geometry(blocks: list[dict]) -> dict:
    """Computed ONCE per page, before region detection starts. Every number here is derived
    from this page's OWN blocks - nothing here is a fixed pixel or page-fraction constant.

    - median_height: this page's own typical block height (a font-size/line-height proxy),
      also the height-anomaly baseline's page-level fallback (see _row_groups) when a region
      has too few blocks of its own to compute a reliable local median.
    - y_gap / x_gap: anchored on the SMALLEST positive gap this page's own blocks already
      show on that axis, not the median. A real page can genuinely mix very different gap
      sizes (a title sitting a full line above its body text, a table whose own cells sit a
      fraction of that apart) - taking the median of both scales together produces a
      compromise value that can reject the smaller, equally-real gap outright (found live: a
      table's own QTY/PO# column gap, smaller than the page's title-to-body gap, fell BELOW
      the median-derived threshold and the two columns silently merged). The smallest
      genuinely positive gap already observed is itself proof that gaps at least that size
      are real on this page; a small 10% margin below it absorbs ordinary bounding-box
      jitter without needing a second, separately-chosen constant. Falls back to a multiple
      of median_height when the page has too few gaps of its own on that axis to measure
      (most real pages have very few genuine column gaps, since most content is
      single-column) - font size is still the right scale to reason from even then.
    """
    if not blocks:
        return {"median_height": _DEGENERATE_GAP, "y_gap": _DEGENERATE_GAP, "x_gap": _DEGENERATE_GAP * 2}
    heights = [max(b["y1"] - b["y0"], 1e-6) for b in blocks]
    median_height = _median(heights)
    y_gaps = _positive_gaps([(b["y0"], b["y1"]) for b in blocks])
    x_gaps = _positive_gaps([(b["x0"], b["x1"]) for b in blocks])
    y_gap = min(y_gaps) * 0.9 if y_gaps else median_height * 0.5
    x_gap = min(x_gaps) * 0.9 if x_gaps else median_height * 1.5
    return {
        "median_height": median_height or _DEGENERATE_GAP,
        "y_gap": y_gap or _DEGENERATE_GAP,
        "x_gap": x_gap or _DEGENERATE_GAP * 2,
    }


def _bbox_of(blocks: list[dict]) -> list[float]:
    if not blocks:
        return [0.0, 0.0, 0.0, 0.0]
    return [
        min(b["x0"] for b in blocks),
        min(b["y0"] for b in blocks),
        max(b["x1"] for b in blocks),
        max(b["y1"] for b in blocks),
    ]


def _bbox_dict(blocks: list[dict]) -> dict:
    x0, y0, x1, y1 = _bbox_of(blocks)
    return {"x0": x0, "y0": y0, "x1": x1, "y1": y1}


# ---------------------------------------------------------------------------------------
# Height-anomaly detection - a continuous per-BLOCK measurement problem, deliberately kept
# separate from the per-ROW hypothesis scoring below (which uses the t-interval mechanism
# instead). Robust local statistics only: median/MAD, compared against this region's own
# distribution, never a single fixed ratio.
# ---------------------------------------------------------------------------------------

def _peer_stats(peers: list[dict]) -> dict:
    """The median/MAD baselines every block in ONE region is judged against - computed ONCE
    per region (not once per block: recomputing a region's own median/MAD for every one of
    its own blocks turns a single region's anomaly check into an O(n^2) pass, which is
    exactly the same shape of risk the row-bucketing sweep-line rewrite was fixing
    elsewhere - a dense real page can have several hundred blocks in one region, and this
    statistic never depends on which block is currently being judged)."""
    heights = [max(p["y1"] - p["y0"], 1e-6) for p in peers]
    median_h = _median(heights)
    widths = [max(p["x1"] - p["x0"], 1e-6) for p in peers]
    median_w = _median(widths)
    confidences = [p["confidence"] for p in peers if p.get("confidence") is not None]
    return {
        "median_h": median_h,
        "mad_h": _mad(heights, median_h),
        "has_heights": bool(heights),
        "median_w": median_w,
        "mad_w": _mad(widths, median_w),
        "median_conf": _median(confidences) if confidences else None,
    }


def _anomaly_signals(block: dict, stats: dict) -> list[bool]:
    """Every signal actually computable for this block, each a plain True/False "does this
    signal think the block is anomalous" - not every signal is always available (Document
    AI's confidence, in particular, may be missing), so the caller judges a MAJORITY of
    whatever came back, never a fixed count.

    A zero MAD for HEIGHT specifically (every peer shares the exact same height) is not "no
    signal" - single-line OCR text within one region overwhelmingly shares one line-height
    regardless of its own content (a font-size property, not a content property), so it is
    the limiting case where a z-score is undefined only because the reference population has
    NO natural variance to divide by at all, and any measurable deviation from a population
    that uniform is significant regardless of its size. Falls back to a plain not-equal test
    (beyond ordinary floating-point/OCR-coordinate jitter) in that case, rather than silently
    contributing nothing - found live in a small, otherwise-clean reproduction of the real
    packing-list bug: 8 of 9 blocks shared one exact height, the 9th (the outlier) did not,
    and a genuine MAD of 0 meant the ordinary z-score path could never fire for it at all.

    WIDTH gets no such fallback: unlike height, a cell's width is expected to vary with its
    OWN text's length even between perfectly ordinary rows (an 8-digit reference number is
    legitimately wider than a 6-digit one) - a zero-MAD width population genuinely carries no
    usable signal, and treating "differs at all" as anomalous there produced a real false
    positive (an ordinary, differently-long real value in an otherwise-uniform column)."""
    signals: list[bool] = []

    h = max(block["y1"] - block["y0"], 1e-6)
    if stats["mad_h"] > 0:
        z = _MAD_TO_SD * (h - stats["median_h"]) / stats["mad_h"]
        signals.append(z > _HEIGHT_Z_CUTOFF)
    elif stats["has_heights"]:
        signals.append(abs(h - stats["median_h"]) > 1e-6)

    if stats["mad_w"] > 0:
        w = max(block["x1"] - block["x0"], 1e-6)
        zw = _MAD_TO_SD * (w - stats["median_w"]) / stats["mad_w"]
        signals.append(abs(zw) > _HEIGHT_Z_CUTOFF)

    median_conf = stats["median_conf"]
    if block.get("confidence") is not None and median_conf is not None:
        # Relative, not an absolute "confidence < 0.5" rule - a page Document AI is
        # generally unsure about (a poor scan) should not flag every block on it merely for
        # matching that page's own (low) normal.
        signals.append(median_conf > 0 and block["confidence"] < median_conf * 0.7)

    return signals


def _is_anomalous(block: dict, stats: dict) -> bool:
    """A tie (exactly half of the available signals agree) counts as anomalous, not merely a
    strict majority - found live: Document AI's own confidence field is usually absent, so
    only 2 of the 3 possible signals (height, width) are normally available at all, and a
    genuinely, massively anomalous block (height z-scores in the hundreds on a real 375-
    block packing-list page) can still land with an ordinary-looking WIDTH by coincidence,
    producing an exact 1-of-2 tie under a strict ">" majority - silently NEVER flagging it,
    however extreme the height signal, purely because only two signals existed to vote at
    all. The two possible mistakes are not symmetric: missing a real outlier lets it swallow
    every row it overlaps (the original packing-list bug this module exists to fix), while
    wrongly flagging an ordinary block only clamps its already-unusual-looking span to the
    local median - a small, reversible cost. That asymmetry is what justifies resolving a
    tie toward "anomalous" rather than away from it; it is a decision-rule choice, not a new
    numeric constant."""
    signals = _anomaly_signals(block, stats)
    if not signals:
        return False
    return sum(signals) >= len(signals) / 2.0


def _row_groups(blocks: list[dict], median_height: float | None = None) -> list[list[dict]]:
    """blocks: everything in one confirmed vertical-cut region, across every column. Buckets
    them into visual rows by real Y-overlap (the same test _table_row_count's own row
    matching already uses) - NOT the touching-counts-as-merged rule _cut_points uses for
    deciding whether to cut at all; a table's own rows must share a genuine overlap, not
    just abut.

    An anomalous block (see _is_anomalous) is matched using a clamped stand-in span centred
    on its own middle, not its real y0/y1 - everything else about it (its actual text, its
    real bbox for column-anchor purposes) is untouched. Every block is judged against every
    OTHER block in this same region, across all its columns together - on a real dense
    table (the packing-list bug this exists to catch had 357 blocks in one region), the
    region as a whole carries a much more reliable sense of "how tall does an ordinary cell
    read here" than any one column alone could, and an outlier severe enough to matter
    shows up as an extreme z-score against that combined population regardless (found live:
    the three real garbled blocks that caused this bug scored height z in the HUNDREDS
    against the full 357-block set - see _is_anomalous's own docstring for why they were
    still missed until a second, independent bug in the vote-counting itself was fixed).
    Clamping never depends on processing order, so it is computed for every block UP FRONT,
    then blocks are swept in order of their EFFECTIVE (possibly clamped) position rather
    than their raw one - this is also what makes the active-window sweep below correct: a
    clamped outlier's effective position can sit earlier on the page than its own raw y0,
    which would otherwise let a bucket appear to close before a not-yet-processed block
    still needed it."""
    if not blocks:
        return []
    heights = [max(b["y1"] - b["y0"], 1e-6) for b in blocks]
    region_median_height = median_height if median_height is not None else _median(heights)
    stats = _peer_stats(blocks)

    prepared: list[tuple[float, float, float, dict]] = []
    for b in blocks:
        raw_height = max(b["y1"] - b["y0"], 1e-6)
        if _is_anomalous(b, stats):
            center = (b["y0"] + b["y1"]) / 2.0
            y0, y1, height = (
                center - region_median_height / 2.0,
                center + region_median_height / 2.0,
                region_median_height,
            )
        else:
            y0, y1, height = b["y0"], b["y1"], raw_height
        prepared.append((y0, y1, height, b))
    prepared.sort(key=lambda t: t[0])

    rows: list[dict] = []
    active_start = 0
    for y0, y1, height, b in prepared:
        # Sweep-line: once a bucket's y1 falls behind the CURRENT block's y0, it can never
        # match this or any later block (processing order is now the effective-position
        # order, guaranteed non-decreasing) - close it permanently instead of rescanning it
        # on every subsequent block. Bounds the inner loop to the small set of buckets that
        # can plausibly still be open, not the full history - avoids the O(n^2) worst case a
        # full rescan has on a dense page (the packing list this bug was found on had 375
        # blocks on one page).
        while active_start < len(rows) and rows[active_start]["y1"] < y0:
            active_start += 1
        placed = None
        for idx in range(active_start, len(rows)):
            row = rows[idx]
            row_height = max(row["y1"] - row["y0"], 1e-6)
            overlap = min(y1, row["y1"]) - max(y0, row["y0"])
            if overlap / min(height, row_height) >= _ROW_OVERLAP_RATIO:
                placed = row
                break
        if placed is None:
            rows.append({"y0": y0, "y1": y1, "items": [b]})
        else:
            placed["items"].append(b)
            placed["y0"] = min(placed["y0"], y0)
            placed["y1"] = max(placed["y1"], y1)
    return [r["items"] for r in sorted(rows, key=lambda r: r["y0"])]


# ---------------------------------------------------------------------------------------
# Structural hypothesis detection - TABLE vs. KEY_VALUE vs. the implicit COLUMNS default,
# scored from the SAME underlying row evidence and compared via the t-interval mechanism.
# ---------------------------------------------------------------------------------------

def _hypothesis_lower_bound(scores: list[float]) -> float | None:
    """scores: one continuous [0,1] evidence value per row (or per block), all in support of
    the SAME hypothesis. Returns the lower bound of a one-sided 90% Student's t confidence
    interval on their true mean, or None when there are too few scores to estimate
    consistency at all (n<2 - a mathematical floor, not a policy: a standard deviation
    cannot be computed from a single observation).

    This is deliberately NOT a Wilson/binomial-proportion interval: an earlier version of
    this module fed continuous per-row scores into Wilson's formula (built for genuine
    binary counts) and found, by direct calculation, that it rejected its own best real
    example - two rows, both scoring ~0.94, unanimous and strong - at a lower bound of only
    0.34, because Wilson's interval is simply wide at n=2 regardless of how the evidence
    looks. The t-interval on the score MEAN gives the right shape instead: n=2 with a high,
    tightly-clustered mean (low sample variance) produces a narrow, high interval - "strong,
    internally consistent evidence is sufficient even when the sample is small" - while n=2
    with a low mean, even if just as tightly clustered, still correctly fails; conflicting
    evidence at any n widens the interval and lowers the bound. No row-count exception of
    any kind is needed for either case; both fall out of the same formula.
    """
    n = len(scores)
    if n < 2:
        return None
    mean = sum(scores) / n
    sd = statistics.stdev(scores)
    se = sd / (n ** 0.5)
    return mean - _t_critical(n - 1) * se


def _hypothesis_upper_bound(scores: list[float]) -> float | None:
    n = len(scores)
    if n < 2:
        return None
    mean = sum(scores) / n
    sd = statistics.stdev(scores)
    se = sd / (n ** 0.5)
    return mean + _t_critical(n - 1) * se


def _spacing_regularity(row_bboxes: list[tuple[float, float]]) -> float | None:
    """row_bboxes: each row's own (y0, y1) span, in row order. Returns a [0,1] score: 1.0
    when every row-to-row gap is the same size (independent, evenly-spaced records - the
    shape a genuine table OR a genuine key:value list both have), falling toward 0 as the
    gaps become irregular (some rows tightly clustered, a bigger gap before the next group -
    the shape a wrapped multi-line ENTITY has, which is what a real side-by-side block reads
    as). Uses the coefficient of variation of the gaps - a smooth, self-referential measure
    (relative to this region's OWN mean gap), not a fixed cutoff.

    Returns None, not a vacuous 1.0, when there are fewer than 2 gaps to compare (fewer than
    3 rows) - regularity genuinely cannot be measured from a single gap, and an earlier
    version of this module manufacturing a "perfectly regular" 1.0 in that situation let a
    bare 2-row region (a genuine label:value pair has exactly this shape) score as strong
    TABLE evidence purely from a term that carried no real information at all. Callers treat
    None as "this term contributes nothing," not as either extreme."""
    gaps = [row_bboxes[i + 1][0] - row_bboxes[i][1] for i in range(len(row_bboxes) - 1)]
    gaps = [max(g, 0.0) for g in gaps]
    if len(gaps) < 2:
        return None
    mean_gap = sum(gaps) / len(gaps)
    if mean_gap <= 0:
        return 1.0
    cov = statistics.stdev(gaps) / mean_gap
    return 1.0 / (1.0 + cov)


def _column_tightness(items: list[dict], column_means: list[float]) -> float:
    """items: the blocks occupying one row (already column-assigned). Returns a [0,1] score
    for how tightly this row's items sit on their own columns' typical centre, relative to
    the SAME region's own column spread - self-referential, not a fixed pixel tolerance."""
    if not items:
        return 0.0
    spread = _mad([sum((b["x0"] + b["x1"]) / 2 for b in [it]) for it in items] + column_means, _median(column_means)) or 1e-6
    scores = []
    for b in items:
        anchor = (b["x0"] + b["x1"]) / 2
        nearest = min(column_means, key=lambda m: abs(anchor - m)) if column_means else anchor
        offset = abs(anchor - nearest)
        scores.append(max(0.0, 1.0 - min(1.0, offset / (spread * 3))))
    return sum(scores) / len(scores)


_LABEL_SUFFIX_CHARS = (":", "-")


def _looks_like_a_label(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and stripped[-1] in _LABEL_SUFFIX_CHARS


def _column_assign(items: list[dict], column_means: list[float]) -> list[dict | None]:
    """items: every block in one row, already known to share this region's column split.
    Returns one cell per discovered column (nearest-anchor assignment), merging two
    fragments that land in the same cell (a wrapped multi-line value Document AI split into
    separate paragraphs) rather than dropping one."""
    cells: list[dict | None] = [None] * len(column_means)
    for b in items:
        anchor = (b["x0"] + b["x1"]) / 2
        nearest = min(range(len(column_means)), key=lambda i: abs(anchor - column_means[i]))
        if cells[nearest] is None:
            cells[nearest] = b
        else:
            cells[nearest] = {**cells[nearest], "text": cells[nearest]["text"] + " " + b["text"]}
    return cells


def _classify_column_region(x_groups: list[list[dict]], geo: dict) -> dict:
    """x_groups: 2+ X-groups from one XY-Cut vertical split, left-to-right. Scores TABLE
    (any column count) and, for an exactly-2-way split, KEY_VALUE, from the same underlying
    per-row evidence, and accepts whichever hypothesis both clears its own bar AND is
    statistically separated from the alternative (see _hypothesis_lower_bound). Neither
    clears -> the pre-existing "columns" default, unchanged.

    Column count never gates entry into the TABLE test - a 2-column region can pass it given
    strong enough grid evidence, and a region with many columns does not pass automatically
    just from having them; row/column REPETITION is what's actually being measured.
    """
    tagged = [dict(b, _col=i) for i, col in enumerate(x_groups) for b in col]
    # Deliberately NOT geo["median_height"] (the whole PAGE's own typical height) - an
    # anomalous block's clamped stand-in span (see _row_groups) needs to match the scale of
    # its OWN region, which can be quite different from the page average (a dense sub-table
    # in small print sitting on a page whose other, unrelated headers set a much larger
    # page-wide median). Leaving it unset lets _row_groups derive it from these SAME tagged
    # blocks - the median is already robust to the small minority of anomalous blocks a
    # clamp is meant to correct in the first place.
    rows = _row_groups(tagged)
    n_cols = len(x_groups)
    fallback = {
        "type": "columns", "bbox": _bbox_of(tagged),
        "columns": [_segment_regions(g, geo) for g in x_groups],
        "debug": {"decision": "columns", "reason": "no hypothesis cleared its bar"},
    }
    if len(rows) < 2:
        fallback["debug"]["reason"] = "fewer than 2 candidate rows - cannot assess consistency"
        return fallback

    column_means = [sum((b["x0"] + b["x1"]) / 2 for b in c) / len(c) for c in x_groups]
    row_cells = [_column_assign(row, column_means) for row in rows]
    row_bboxes = [(min(b["y0"] for b in row), max(b["y1"] for b in row)) for row in rows]
    spacing_score = _spacing_regularity(row_bboxes)

    populated_sets = [{i for i, c in enumerate(cells) if c is not None} for cells in row_cells]
    multi_col_sets = [s for s in populated_sets if len(s) >= 2]
    common_columns: set[int] = set()
    if multi_col_sets:
        common_columns = set.intersection(*multi_col_sets)

    # "Every row has all its columns populated" is only evidence FOR a repeating-record
    # table when a row COULD plausibly have come back with a column missing - true whenever
    # 3+ columns exist (a ragged TOTAL row is a real, common case), but at exactly 2 columns
    # a populated row is guaranteed by the column-assignment step itself (see
    # _classify_column_region's own docstring / _column_assign): a genuine key:value pair
    # satisfies "both columns populated" exactly as trivially as a genuine 2-column table
    # does, so this term carries zero discriminating bits at n_cols==2 and is excluded there
    # - TABLE's only usable evidence at exactly 2 columns is row-to-row spacing regularity,
    # and only once there are enough rows (3+) to measure it at all (see
    # _spacing_regularity). This is a fact about what the measurement can distinguish, not a
    # fixed column-count floor re-introduced by another name: a 2-column region can still
    # become "table" here whenever real, measurable spacing evidence supports it.
    if n_cols == 2:
        # A row that ISN'T fully populated at exactly 2 columns still means something (no
        # real pairing was found on that row at all) - only "fully populated" is
        # uninformative here, not "under-populated."
        table_scores = (
            [spacing_score if len(populated) == 2 else 0.0 for populated in populated_sets]
            if spacing_score is not None else None
        )
    else:
        population_scores = []
        for populated in populated_sets:
            if len(populated) < 2:
                population_scores.append(0.0)
            elif common_columns:
                population_scores.append(len(populated & common_columns) / len(common_columns))
            else:
                population_scores.append(len(populated) / n_cols)
        if spacing_score is not None:
            table_scores = [(s + spacing_score) / 2.0 for s in population_scores]
        else:
            table_scores = population_scores
    table_lower = _hypothesis_lower_bound(table_scores) if table_scores is not None else None
    table_upper = _hypothesis_upper_bound(table_scores) if table_scores is not None else None

    kv_lower = kv_upper = None
    kv_pairs: list[tuple[dict, dict]] | None = None
    if n_cols == 2:
        shape_ok = all(len(populated) == 2 and all(c is not None for c in cells)
                       for populated, cells in zip(populated_sets, row_cells))
        if shape_ok:
            kv_scores = []
            for cells in row_cells:
                label, value = cells[0], cells[1]
                label_evidence = 1.0 if _looks_like_a_label(label["text"]) else 0.3
                tightness = _column_tightness([label, value], column_means)
                if spacing_score is not None:
                    kv_scores.append((label_evidence + spacing_score + tightness) / 3.0)
                else:
                    kv_scores.append((label_evidence + tightness) / 2.0)
            kv_lower = _hypothesis_lower_bound(kv_scores)
            kv_upper = _hypothesis_upper_bound(kv_scores)
            kv_pairs = [(cells[0], cells[1]) for cells in row_cells]

    debug_scores = {
        "table": {"lower": table_lower, "upper": table_upper},
        "key_value": {"lower": kv_lower, "upper": kv_upper} if kv_lower is not None else None,
    }

    candidates = []
    if table_lower is not None and table_lower > 0.5:
        candidates.append(("table", table_lower, table_upper))
    if kv_lower is not None and kv_lower > 0.5:
        candidates.append(("key_value", kv_lower, kv_upper))

    if not candidates:
        fallback["debug"] = {"decision": "columns", "reason": "no hypothesis lower bound exceeded 0.5", "scores": debug_scores}
        return fallback

    table_candidate = next((c for c in candidates if c[0] == "table"), None)
    kv_candidate = next((c for c in candidates if c[0] == "key_value"), None)

    if kv_candidate is not None and table_candidate is not None:
        # Both cleared the 0.5 bar on the SAME 2-column region. KEY_VALUE is the more
        # specific hypothesis for an exactly-2-column shape (a genuine table of any width
        # is already the general case; a 2-column region is the ONLY place key_value can
        # ever apply at all), so it is the natural tie-break rather than an arbitrary pick -
        # TABLE only overrides it when its own interval sits entirely, unambiguously above
        # key_value's (its lower bound clears key_value's own upper bound), i.e. the evidence
        # genuinely separates them in TABLE's favour, not merely a higher point estimate.
        if table_candidate[1] > kv_candidate[2]:
            winner, winner_lower, winner_upper = table_candidate
        else:
            winner, winner_lower, winner_upper = kv_candidate
    else:
        candidates.sort(key=lambda c: c[1], reverse=True)
        winner, winner_lower, winner_upper = candidates[0]
        if len(candidates) > 1:
            _, _, runner_upper = candidates[1]
            if runner_upper is not None and winner_lower <= runner_upper:
                fallback["debug"] = {
                    "decision": "columns", "reason": "competing hypotheses overlap - ambiguous",
                    "scores": debug_scores,
                }
                return fallback

    if winner == "table":
        populated_blocks = [b for cells in row_cells for b in cells if b is not None]
        return {
            "type": "table", "bbox": _bbox_of(populated_blocks), "columns": column_means,
            "rows": row_cells, "debug": {"decision": "table", "scores": debug_scores},
        }
    return {
        "type": "key_value", "bbox": _bbox_of(tagged), "pairs": kv_pairs,
        "debug": {"decision": "key_value", "scores": debug_scores},
    }


def _table_block(grid: list[list[dict | None]]) -> dict:
    """grid: ragged, from _classify_column_region - each row the same width, empty cells as
    None. Builds one synthetic block whose text is a Markdown table (first row as header,
    same convention _table_markdown falls back to when Document AI declared no header row
    of its own) - and whose bbox spans every populated cell, so it behaves as one atomic
    unit to any recursion around it, exactly like a real Document AI table already does."""
    def cell_text(b: dict | None) -> str:
        return b["text"].replace("\n", " ").strip() if b else ""

    width = len(grid[0])
    header = [cell_text(c) for c in grid[0]]
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join(["---"] * width) + "|"]
    for row in grid[1:]:
        lines.append("| " + " | ".join(cell_text(c) for c in row) + " |")
    all_blocks = [b for row in grid for b in row if b is not None]
    return {**_bbox_dict(all_blocks), "kind": "table", "text": "\n".join(lines)}


def _kv_line_block(label: dict, value: dict) -> dict:
    label_text = label["text"].strip()
    value_text = value["text"].strip()
    sep = "" if _looks_like_a_label(label_text) else ":"
    return {**_bbox_dict([label, value]), "kind": "key_value", "text": f"{label_text}{sep} {value_text}".strip()}


# ---------------------------------------------------------------------------------------
# XY-Cut region detection - unchanged mechanism from earlier versions, now threading the
# page's own adaptive geometry through every recursive call instead of reading fixed
# module-level constants.
# ---------------------------------------------------------------------------------------

def _merged_coverage(spans: list[tuple[float, float]]) -> list[list[float]]:
    """spans: (start, end) intervals, any order. Returns the merged (non-overlapping, sorted)
    coverage intervals - the gaps BETWEEN these are the only candidate cut points, since a
    cut point inside any single span would cut through that block."""
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


def _column_means(blocks: list[dict], geo: dict) -> list[float] | None:
    """If this exact set of blocks has a genuine vertical split of its own, returns its
    sorted column X-anchors (means); None if no split is found. Used only to test whether
    two ADJACENT horizontal bands are really one table torn apart by a real-but-ordinary
    row-to-row gap - see _merge_table_bands."""
    x_groups = _split_at(blocks, _cut_points([(b["x0"], b["x1"]) for b in blocks], geo["x_gap"]), "x")
    if x_groups is None:
        return None
    return sorted(sum((b["x0"] + b["x1"]) / 2 for b in c) / len(c) for c in x_groups)


def _anchors_match(a: list[float], b: list[float], geo: dict) -> bool:
    return len(a) == len(b) and all(abs(x - y) <= geo["x_gap"] / 2 for x, y in zip(a, b))


def _merge_table_bands(y_groups: list[list[dict]], geo: dict) -> list[dict]:
    """A genuine table's own rows can have a real, ordinary row-to-row gap slightly larger
    than the page's own adaptive y_gap (ordinary body-text paragraph spacing, not a bridging
    fluke) - the row-first horizontal split then tears it into several single/few-row bands,
    each with too little evidence on its own, even though every one of them shares the exact
    same column positions. This looks ACROSS already-separated sibling bands (never across
    unrelated content further away - only strictly adjacent ones) for a run that shares the
    same column anchors, and retries classification on their UNION before giving up on each
    band individually. Returns the final, page-ordered region list."""
    out: list[dict] = []
    i = 0
    while i < len(y_groups):
        anchors = _column_means(y_groups[i], geo)
        run = [y_groups[i]]
        j = i + 1
        while anchors is not None and j < len(y_groups):
            next_anchors = _column_means(y_groups[j], geo)
            if next_anchors is None or not _anchors_match(anchors, next_anchors, geo):
                break
            run.append(y_groups[j])
            j += 1
        if len(run) >= 2:
            merged_blocks = [b for g in run for b in g]
            x_groups = _split_at(merged_blocks, _cut_points([(b["x0"], b["x1"]) for b in merged_blocks], geo["x_gap"]), "x")
            if x_groups is not None:
                region = _classify_column_region(x_groups, geo)
                if region["type"] in ("table", "key_value"):
                    out.append(region)
                    i = j
                    continue
        # No compatible run, or the merged run still wasn't a table/key_value - fall back to
        # each band's own, already-verified independent handling. Only ONE band is consumed
        # here (not the whole failed run), so the next iteration re-tries matching fresh from
        # the very next band rather than giving up on the rest of the run too.
        out.extend(_segment_regions(y_groups[i], geo))
        i += 1
    return out


def _segment_regions(blocks: list[dict], geo: dict) -> list[dict]:
    """REGION DETECTION - see module docstring. Returns a flat, page-ordered list of typed
    region nodes:
      {"type": "text", "bbox": [...], "children": [block, ...]}
      {"type": "columns", "bbox": [...], "columns": [[Region, ...], [Region, ...], ...]}
      {"type": "table", "bbox": [...], "columns": [x_anchor, ...], "rows": [[block|None, ...], ...]}
      {"type": "key_value", "bbox": [...], "pairs": [(label_block, value_block), ...]}
    Says nothing yet about how a "columns" region's own columns get concatenated - that is
    _reading_order's job, entirely separately."""
    if len(blocks) <= 1:
        return [{"type": "text", "bbox": _bbox_of(blocks), "children": list(blocks)}]

    y_groups = _split_at(blocks, _cut_points([(b["y0"], b["y1"]) for b in blocks], geo["y_gap"]), "y")
    if y_groups is not None:
        return _merge_table_bands(y_groups, geo)

    x_groups = _split_at(blocks, _cut_points([(b["x0"], b["x1"]) for b in blocks], geo["x_gap"]), "x")
    if x_groups is not None:
        return [_classify_column_region(x_groups, geo)]

    # Neither axis found a genuine gap - an irreducible cluster. Keep plain Y order rather
    # than guess; this is exactly what today's layout_text would already have produced.
    return [{"type": "text", "bbox": _bbox_of(blocks), "children": sorted(blocks, key=lambda b: b["y0"])}]


def _reading_order(regions: list[dict]) -> list[dict]:
    """READING ORDER - walks a region tree from _segment_regions and produces the final flat,
    ordered sequence of renderable blocks - a "text" region contributes its own blocks, a
    "columns" region contributes each column's own reading order in turn (left to right), a
    "table" region contributes ONE synthetic block holding the rendered Markdown table, a
    "key_value" region contributes one "Label: Value" synthetic block per pair, in row
    order."""
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
        elif kind == "key_value":
            out.extend(_kv_line_block(label, value) for label, value in region["pairs"])
    return out


def build_structured_text(blocks: list[dict]) -> str | None:
    """blocks: one page's worth of Document AI paragraphs AND tables, already rendered/typed
    by the caller (docai.py) - each a dict with at least {"kind": "paragraph" | "table",
    "x0", "y0", "x1", "y1", "text"}, and optionally "confidence" (Document AI's own
    per-paragraph confidence, used only by height-anomaly detection).

    Returns the joined string in the reconstructed reading order, or None when the result
    came out identical to plain top-to-bottom order - the caller falls back to the existing
    layout_text untouched in that case (also the outcome of any internal error), so a page
    this engine cannot help, or gets wrong, is guaranteed to read exactly as it always has.
    """
    try:
        if len(blocks) < 2:
            return None
        geo = _page_geometry(blocks)
        tagged = [dict(b, id=i) for i, b in enumerate(blocks)]
        plain_order = sorted(tagged, key=lambda b: b["y0"])
        regions = _segment_regions(list(plain_order), geo)
        ordered = _reading_order(regions)
        # A synthetic table/key-value block carries no "id" - it replaces several original
        # blocks with one, so .get() rather than a bare [] lookup is required here; it can
        # never equal plain_order's own ids either way, which is correct - collapsing
        # several blocks into one structure is always a change worth returning.
        if [b.get("id") for b in ordered] == [b["id"] for b in plain_order]:
            return None
        return "\n\n".join(b["text"] for b in ordered) or None
    except Exception:  # noqa: BLE001 — structure is a bonus, never a hard dependency
        return None


def describe_regions(blocks: list[dict]) -> dict:
    """DEBUG-ONLY structural representation of one page - never consumed by the real
    extraction pipeline and never changes the business JSON. Wired only into field_marks.py's
    own debug=true test-extract path, for verifying/validating the region tree, the
    competing-hypothesis scores, and any fallback reason directly."""
    try:
        geo = _page_geometry(blocks)
        tagged = [dict(b, id=i) for i, b in enumerate(blocks)]
        plain_order = sorted(tagged, key=lambda b: b["y0"])
        regions = _segment_regions(list(plain_order), geo)
        return {"regions": [_region_to_json(r) for r in regions], "geometry": geo}
    except Exception:  # noqa: BLE001
        return {"regions": [], "geometry": {}}


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
        out = {
            "type": "columns",
            "bbox": region["bbox"],
            "columns": [[_region_to_json(r) for r in col] for col in region["columns"]],
        }
        if "debug" in region:
            out["debug"] = region["debug"]
        return out
    if kind == "table":
        out = {
            "type": "table",
            "bbox": region["bbox"],
            "columns": region["columns"],
            "rows": [[(c["text"] if c else None) for c in row] for row in region["rows"]],
        }
        if "debug" in region:
            out["debug"] = region["debug"]
        return out
    if kind == "key_value":
        out = {
            "type": "key_value",
            "bbox": region["bbox"],
            "pairs": [{"label": label["text"], "value": value["text"]} for label, value in region["pairs"]],
        }
        if "debug" in region:
            out["debug"] = region["debug"]
        return out
    return region
