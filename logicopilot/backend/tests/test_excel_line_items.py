"""Excel line items: read in 40-row parts that each carry the real column-heading row and an
item count, and the model's repeated / partial lines taken out against the sheet (JOB-CEF1C1)."""
from app.api.v1.jobs import _dedupe_excel_rows
from app.core.excel_text import Sheet, header_row_index, numbered_items, sheet_row_chunks


def _invoice(n_items=110):
    rows = [(1, [("A", "COMMERCIAL INVOICE"), ("B", "COMMERCIAL INVOICE"), ("C", "COMMERCIAL INVOICE")]),
            (3, [("A", "Shipper / Exporter"), ("H", "Commercial Invoice Number"), ("I", "X2026110602")]),
            (12, [("A", "ATTN:"), ("B", "S.Rajapandi,"), ("H", "Payment Term"), ("I", "Net 90 days")]),
            (13, [("A", "Tel:"), ("B", "9790302510"), ("C", "9790302510")]),
            (18, [("A", "ITEM"), ("B", "Loading NO."), ("C", "PRODUCT NUMBER"), ("D", "DESCRIPTION"),
                  ("G", "UNIT PRICE(USD)"), ("H", "QTY"), ("I", "AMOUNT")])]
    for i in range(1, n_items + 1):
        rows.append((18 + i, [("A", str(i)), ("B", "X2026110602"), ("C", f"P{i:04d}"), ("D", f"PART {i}"),
                              ("G", "0.5"), ("H", str(100 * i)), ("I", str(50 * i))]))
    rows.append((18 + n_items + 1, [("G", "TOTAL"), ("H", "999"), ("I", "999")]))
    return Sheet(name="Total Proforma Invoice", rows=rows)


def test_heading_row_found_below_the_title_block():
    sheet = _invoice()
    assert sheet.rows[header_row_index(sheet)][0] == 18          # not the ATTN / Tel block
    assert numbered_items(sheet.rows[header_row_index(sheet) + 1:]) == 110   # TOTAL not counted


def test_110_items_read_in_three_parts_each_with_the_heading_and_a_count():
    parts = sheet_row_chunks(_invoice())
    assert [h for _t, h in parts] == [40, 40, 30]
    for text, _h in parts:
        assert "R18 | A: ITEM" in text                            # column names in every part
    assert "C: P0041" in parts[1][0] and "C: P0040" not in parts[1][0]


def test_short_sheet_is_one_part():
    parts = sheet_row_chunks(_invoice(12))
    assert len(parts) == 1 and parts[0][1] == 12


LABELS = ["item_material_code", "product_description", "item_quantity", "product_amount"]


def _row(code, desc, qty, amt):
    return dict(zip(LABELS, [code, desc, qty, amt]))


def test_model_repeats_and_stray_copies_are_dropped():
    sheet = _invoice(10)
    rows = [_row(f"P{i:04d}", f"PART {i}", str(100 * i), str(50 * i)) for i in range(1, 11)]
    rows += [_row("P0004", "PART 4", "400", "200"), _row("P0005", "PART 5", "500", "250")]   # repeats
    rows += [_row("", "", "700", "350")]                                                     # stray copy
    kept = _dedupe_excel_rows(rows, sheet, LABELS)
    assert len(kept) == 10 and [r["item_material_code"] for r in kept] == [f"P{i:04d}" for i in range(1, 11)]


def test_a_line_the_sheet_really_has_twice_is_kept():
    sheet = _invoice(3)
    sheet.rows.insert(-1, (99, [("A", "4"), ("B", "X2026110602"), ("C", "P0002"), ("D", "PART 2"),
                                ("G", "0.5"), ("H", "200"), ("I", "100")]))
    rows = [_row("P0001", "PART 1", "100", "50"), _row("P0002", "PART 2", "200", "100"),
            _row("P0003", "PART 3", "300", "150"), _row("P0002", "PART 2", "200", "100")]
    assert len(_dedupe_excel_rows(rows, sheet, LABELS)) == 4
