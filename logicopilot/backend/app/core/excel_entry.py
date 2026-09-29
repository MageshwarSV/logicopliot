"""Excel-based ERP entry.

Some ERPs do not want fifty typed fields — they take a bulk import: you reach an upload screen,
hand over a workbook and press a button. For those, a recorded script that types box by box is
the wrong shape entirely. This module turns a job's extracted data into the workbook the ERP
expects, so the script only has to attach one file.

Two ways a customer's sheet gets its shape:

  blank    — we create the sheet, and the column order is whatever the mapping says
  template — the customer already has a workbook the ERP accepts. Its header row is read, and
             each heading is mapped to a data field. The produced file keeps that exact layout,
             because the ERP is matching on those headings.

Line items are the reason this is not a one-row write. An invoice with fourteen products has to
become fourteen rows, and a field marked per-row carries one value per line. A field that is
NOT per-row repeats down every row, which is what an ERP import expects for a job-level value
like the BE number.

EVERYTHING HERE FAILS LOUDLY. A wrong figure in a customs import is worse than a job that
stops, so a missing template, a renamed sheet or an unmappable column raises rather than
quietly producing a file that looks plausible.
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# openpyxl is imported lazily so the rest of the app - and every existing job - keeps working on
# a server where it has not been installed yet. Anything that needs it fails with a message that
# says what to install rather than an ImportError at start-up.
_MISSING = (
    "Excel entry needs the openpyxl package. Add it with `uv add openpyxl` in backend/ and "
    "restart the backend."
)

# A leading one of these makes Excel treat the cell as a formula. A document value that starts
# with one is data, not a calculation, and must be written as text.
_FORMULA_START = ("=", "+", "-", "@")


def _openpyxl():
    try:
        import openpyxl  # noqa: PLC0415

        return openpyxl
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(_MISSING) from exc


def read_headers(path: str | Path, sheet: str | None = None,
                 header_row: int = 1) -> dict:
    """What columns does this workbook expect?

    Returns {"sheets": [...], "sheet": used, "header_row": n, "headers": [...]}. A blank
    heading is kept, with its column letter, because an ERP template often has a spacer column
    and dropping it would shift every column after it.

    Read WITHOUT read_only: in read-only mode openpyxl hands back an `EmptyCell` for a cell
    nobody ever typed into, and `EmptyCell` has no `column_letter` - which crashed on the very
    templates this is for. Only the header row is touched, so the cost is the same.
    """
    xl = _openpyxl()
    from openpyxl.utils import get_column_letter

    wb = xl.load_workbook(filename=str(path), data_only=True)
    try:
        sheets = list(wb.sheetnames)
        name = sheet if sheet in sheets else (sheets[0] if sheets else None)
        if name is None:
            return {"sheets": [], "sheet": None, "header_row": header_row, "headers": []}
        ws = wb[name]
        headers: list[dict] = []
        for idx in range(1, (ws.max_column or 0) + 1):
            value = ws.cell(row=header_row, column=idx).value
            headers.append({
                "column": get_column_letter(idx),
                "header": "" if value is None else str(value).strip(),
            })
        # Trailing empties are padding, not columns - drop them, but keep gaps in the middle.
        while headers and not headers[-1]["header"]:
            headers.pop()
        return {"sheets": sheets, "sheet": name, "header_row": header_row, "headers": headers}
    finally:
        try:
            wb.close()
        except Exception:  # noqa: BLE001
            pass


# Serial numbers the ERP wants but no document carries. A column can be mapped to one of these
# instead of a data field:
#   #row      the row's own number within its sheet - ItemSrNo on ITEMS, InvSrNo on INVOICES
#   #invoice  which invoice this row belongs to - InvSrNo on ITEMS
# They are generated per row rather than read, so they cannot be extracted and must not be
# offered as if they were.
SERIAL_ROW = "#row"
SERIAL_INVOICE = "#invoice"
SERIALS = {
    SERIAL_ROW: "row number (ItemSrNo / InvSrNo)",
    SERIAL_INVOICE: "invoice number this row belongs to (InvSrNo)",
}


# A sheet's row scope decides how many rows it gets:
#   job  - exactly one row (GENERAL, SHIPMENT, CONTAINERS)
#   line - one row per line item, driven by the per-row fields mapped into it (ITEMS)
#   fixed - a block of rows the sheet always carries, spelled out in the mapping itself
#           (STATEMENT's declaration codes, EXCHANGE_RATE's one row per currency)
_SCOPES = ("job", "line", "fixed", "invoice")

# Reserved key in the `rows` mapping: which invoice-set each line belongs to, in the same
# order as every other column. Not a field the customer maps — it is how the line list says
# "these five products were billed on invoice 2". Named with underscores so it can never
# collide with a real column label.
LINE_SET_KEY = "__invoice_set__"

# Reserved key: the job-level values read off EACH invoice, one dict per set in set order.
# An invoice-scoped sheet writes a row from each. Absent on a single-invoice job, which is
# why such a job's workbook comes out byte-for-byte as it did before.
SET_VALUES_KEY = "__invoice_values__"


def _set_values(rows: dict | None) -> list[dict]:
    seq = (rows or {}).get(SET_VALUES_KEY)
    return [d for d in seq if isinstance(d, dict)] if isinstance(seq, list) else []


def rename_field_in_excel_config(config: dict | None, old_label: str, new_label: str) -> bool:
    """Rewrite every excel_config column mapped to old_label so it points at new_label
    instead - called when a mark or custom field is renamed.

    Without this, a rename left every "field": "<old_label>" mapping in place, silently
    blanking that Excel column on every job's export from then on: sheet_plans/
    _rows_for_plan look a column's value up purely by whatever label its mapping currently
    names, and a mapping naming a label nothing extracts under anymore just quietly
    resolves to "" - _column_fill's own empty-workbook guard only refuses a build when
    EVERY column comes out empty, so a single blanked column among several passes it
    silently. This directly contradicts edit_custom_field's own docstring, which promises
    "Renaming is safe."

    Mutates `config` in place (the caller still owns persisting it - a JSON column's
    in-place mutation needs its own flag_modified, not something this helper should assume
    the caller does or doesn't want). Returns whether anything was actually changed.
    """
    if not config or not old_label or old_label == new_label:
        return False
    changed = False

    def _rewrite(columns) -> None:
        nonlocal changed
        for col in columns or []:
            if isinstance(col, dict) and (col.get("field") or "").strip() == old_label:
                col["field"] = new_label
                changed = True

    sheets = config.get("sheets")
    if isinstance(sheets, list):
        for entry in sheets:
            if isinstance(entry, dict):
                _rewrite(entry.get("columns"))
    else:
        _rewrite(config.get("columns"))
    return changed


def sheet_plans(config: dict) -> list[dict]:
    """Every sheet this config fills, as a list of plans.

    Accepts both shapes. The original config held one sheet at the top level; a template with
    several sheets to fill carries a `sheets` list instead. Normalising here means the writer
    and the validators only ever deal with one shape.
    """
    if not config:
        return []
    raw = config.get("sheets")
    if isinstance(raw, list) and raw:
        plans = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            scope = (entry.get("scope") or "job").strip().lower()
            plans.append({
                "sheet": entry.get("sheet"),
                "header_row": int(entry.get("header_row") or 1),
                "scope": scope if scope in _SCOPES else "job",
                "columns": entry.get("columns") or [],
            })
        return plans
    # the original single-sheet shape
    return [{
        "sheet": config.get("sheet"),
        "header_row": int(config.get("header_row") or 1),
        # A single-sheet config was always written one row per line item when per-row fields
        # were mapped, so keep that behaviour rather than silently changing it.
        "scope": "line",
        "columns": config.get("columns") or [],
    }]


def _literals(col: dict) -> list | None:
    """The literal(s) a column carries, if any.

    Most of what a finished ICEGATE import holds is constant - '0.00', 'KGS', 'AV', 'N'. Those
    are extracted from nothing, and a custom field per zero would fill the operator's screen
    with two dozen questions nobody ever answers. `value` is one literal for every row of the
    sheet; `values` is a list, one per row.
    """
    seq = col.get("values")
    if isinstance(seq, list) and seq:
        return ["" if v is None else str(v) for v in seq]
    v = col.get("value")
    if v is not None and str(v) != "":
        return [str(v)]
    return None


def _rows_for_plan(plan: dict, values: dict, rows: dict) -> list[list]:
    """The data rows for ONE sheet, in its own column order.

    scope="job"   exactly one row. A job-level sheet must not repeat because one of its columns
                  happens to be fed by a per-line field.
    scope="line"  one row per line item.
    scope="fixed" as many rows as the longest literal list in the mapping - a block the sheet
                  always carries, like STATEMENT's five declaration codes.
    scope="invoice" one row per invoice on the job. A shipment covered by three invoices is
                  ONE customs entry with three rows on its INVOICES sheet, each carrying that
                  invoice's own number, date and value.
    """
    cols = plan["columns"]
    labels = [c.get("field") or "" for c in cols]
    lits = [_literals(c) for c in cols]
    # Several ICEGATE columns are shorter than the text that feeds them - General_Description
    # takes the first 60 characters of the product description. Writing the full string means
    # the import is rejected for an over-length field, so a column may cap its own width.
    caps = [int(c.get("max_len") or 0) or None for c in cols]
    depth = 1
    if plan["scope"] == "line":
        # Starts at 0, not the shared default of 1: a job with genuinely no line items at
        # all (every per-row label's own rows list empty or absent) must produce ZERO rows
        # on a line-scoped sheet, not one phantom row of blanks under a serial number that
        # names a line item which was never there. Any label that DOES carry rows still
        # sets the real depth exactly as before.
        depth = 0
        for label in labels:
            if label in SERIALS:
                continue          # a serial follows the row count, it does not set it
            seq = rows.get(label)
            if isinstance(seq, list):
                depth = max(depth, len(seq))
    elif plan["scope"] == "fixed":
        depth = max((len(x) for x in lits if x), default=0)
    elif plan["scope"] == "invoice":
        # One row per invoice. With a single invoice this is one row, exactly as scope="job"
        # always produced, so an existing template's INVOICES sheet is unchanged.
        depth = max(1, len(_set_values(rows)))
    out: list[list] = []
    for i in range(depth):
        line: list = []
        # On an invoice-scoped sheet, a job-level label takes THAT invoice's value.
        per_set = _set_values(rows)
        row_values = per_set[i] if plan["scope"] == "invoice" and i < len(per_set) else None
        for label, lit in zip(labels, lits):
            if lit is not None:
                # One literal repeats down the sheet; a list gives each row its own value.
                raw = lit[i] if i < len(lit) else (lit[0] if len(lit) == 1 else "")
                # A literal may name a field instead of holding a value, using the same
                # [[field]] form the engine uses elsewhere. EXCHANGE_RATE needs it: row one is
                # always the rupee at 1.000000, row two is the month's customs rate, which has
                # to live in one editable place rather than inside the mapping.
                if raw.startswith("[[") and raw.endswith("]]"):
                    raw = str(values.get(raw[2:-2], "") or "")
                line.append(raw)
                continue
            if not label:
                line.append("")
                continue
            if label == SERIAL_ROW:
                # 1-based position in this sheet: ItemSrNo down ITEMS, InvSrNo down INVOICES.
                line.append(str(i + 1))
                continue
            if label == SERIAL_INVOICE:
                # Which invoice an item belongs to. Extraction does not currently record that
                # per item, so with a single invoice every item is 1. When a job really has
                # several invoices this needs the extractor to tag each item with its invoice -
                # writing a guess here would attach items to the wrong invoice.
                line.append(str(invoice_of(i, values, rows)))
                continue
            if row_values is not None:
                # This row IS invoice i+1, so its own reading of the field wins; the job-wide
                # value is the fallback for anything that document does not carry.
                line.append(row_values.get(label, values.get(label, "")))
                continue
            seq = rows.get(label)
            if isinstance(seq, list) and seq:
                line.append(seq[i] if i < len(seq) else "")
            else:
                line.append(values.get(label, ""))
        # Apply each column's own width limit, if it has one.
        line = [(v if cap is None or v is None or len(str(v)) <= cap else str(v)[:cap])
                for v, cap in zip(line, caps)]
        out.append(line)
    return out


def invoice_of(index: int, values: dict, rows: dict) -> int:
    """Which invoice serial a line item belongs to.

    The rule given: InvSrNo follows the invoice count, ItemSrNo the item's place in the list.

    This used to return 1 always, because nothing in the extracted data tied an item to an
    invoice — the note here said "a job with several needs the extractor to record the invoice
    per item; until then this stays at 1 rather than distributing items by guesswork". The
    extractor now does record it: documents are paired on their invoice numbers and every line
    carries the set it came from, which arrives under LINE_SET_KEY alongside the columns.

    Still falls back to 1 when nothing says otherwise, which is the ordinary single-invoice
    job — a guess is never made here.
    """
    del values
    seq = (rows or {}).get(LINE_SET_KEY)
    if isinstance(seq, list) and 0 <= index < len(seq):
        try:
            return max(1, int(seq[index]))
        except (TypeError, ValueError):
            return 1
    return 1


def _rows_for(config: dict, values: dict, rows: dict) -> list[list]:
    """The data rows, in the column order the config lays out.

    One row per line item. A per-row field contributes its own value per line; a job-level
    field repeats, which is what an import expects - the BE number belongs on every line.
    """
    columns = config.get("columns") or []
    labels = [c.get("field") or "" for c in columns]
    depth = 1
    for label in labels:
        seq = rows.get(label)
        if isinstance(seq, list):
            depth = max(depth, len(seq))
    out: list[list] = []
    for i in range(depth):
        line: list = []
        for label in labels:
            if not label:
                line.append("")           # a spacer column in the customer's own template
                continue
            seq = rows.get(label)
            if isinstance(seq, list) and seq:
                line.append(seq[i] if i < len(seq) else "")
            else:
                line.append(values.get(label, ""))
        out.append(line)
    return out


def _write(ws, row: int, col: int, value) -> None:
    """Put one value in a cell as TEXT, never as a formula or a number.

    Two reasons this is not a plain assignment:

    * A document value can start with "=", "+", "-" or "@" - a part code, a negative charge, an
      email-looking reference. Assigned normally, Excel stores it as a FORMULA, and the ERP then
      reads an error or an empty cell instead of the value.
    * Everything is kept as text on purpose. A customs reference can carry leading zeros
      ("007") and a container number is not an arithmetic quantity; turning either into a number
      silently changes the data. If a particular ERP import needs real numbers in a column, that
      is a per-column decision to add deliberately, not a guess made here.
    """
    text = "" if value is None else str(value)
    cell = ws.cell(row=row, column=col)
    if not text:
        # NOTHING, not an empty string. Writing "" produced <c r="V2" t="inlineStr"/> - a cell
        # that EXISTS and declares itself a string while holding no characters. An ERP import
        # reads that as "a value was supplied, and it is 0 characters long" and refuses the
        # file: "Entry should be of 2 characters" on a column meant to be left blank. The
        # workbooks the ERP accepts leave those cells genuinely absent.
        cell.value = None
        cell.data_type = "n"
        return
    cell.value = text
    if text.startswith(_FORMULA_START):
        # Assigning the value already made openpyxl class it as a formula ('f'). Forcing the
        # type back to string is what actually writes it as text - there is no public
        # "explicit value" call in openpyxl 3.x, and quotePrefix alone does not change the type.
        cell.data_type = "s"


def _safe_name(name: str, fallback: str = "import.xlsx") -> str:
    """A file name an ERP file picker will accept, with the extension intact."""
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", (name or "").strip()) or fallback
    if not base.lower().endswith((".xlsx", ".xlsm")):
        base += ".xlsx"
    return base


def build_for_job(
    config: dict,
    values: dict,
    rows: dict | None,
    out_dir: str | Path,
    template_path: str | Path | None = None,
    name_as: str | None = None,
) -> Path:
    """Write the workbook this job should hand to the ERP, and return its path.

    Fills EVERY sheet the config lists. With `source="template"` the customer's own file is
    opened and each sheet written under its own header row, so the layout the ERP matches on
    survives. With `source="blank"` a fresh workbook is written, one worksheet per plan.
    """
    xl = _openpyxl()
    rows = rows or {}
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Named after the JOB when the caller knows which job it is for. Every job already gets
    # its own folder, so nothing was ever overwritten - but the files all shared the template's
    # name, so one pulled off the server said nothing about which entry it belonged to. The
    # recorded upload step is unaffected: it finds the file by the `excel_import` alias, never
    # by its name.
    stem = config.get("file_name") or "import.xlsx"
    suffix = Path(_safe_name(stem)).suffix or ".xlsx"
    target = out_dir / _safe_name(f"{name_as}{suffix}" if name_as else stem)

    plans = sheet_plans(config)
    if not plans:
        raise RuntimeError(
            "Excel entry is switched on for this template but no sheets are configured, so "
            "there would be nothing to import. Map at least one sheet on the ERP Entry step."
        )
    if not any(p["columns"] for p in plans):
        # A sheet with an empty column list is a different mistake from a sheet whose columns
        # are all unmapped, and the message has to say which so it can be acted on.
        raise RuntimeError(
            "Excel entry is switched on for this template but no columns are mapped, so the "
            "sheet would be empty. Map at least one column to a data field."
        )
    live = [p for p in plans
            if any((c.get("field") or "").strip() or _literals(c) is not None
                   for c in p["columns"])]
    if not live:
        raise RuntimeError(
            "Every configured column is unmapped, so the workbook would carry no data. Map at "
            "least one column to a data field."
        )
    source = config.get("source") or "blank"

    if source == "template":
        if not template_path or not Path(template_path).exists():
            raise RuntimeError(
                "This template set is set to use the customer's own import workbook, but that "
                "file is missing on the server. Re-upload it on the ERP Entry step."
            )
        wb = xl.load_workbook(filename=str(template_path))
        written = []
        for plan in live:
            name = plan["sheet"]
            if name and name not in wb.sheetnames:
                raise RuntimeError(
                    f"The workbook no longer has a sheet called {name!r} (it has "
                    f"{', '.join(wb.sheetnames)}). Re-upload it on the ERP Entry step."
                )
            ws = wb[name] if name else wb[wb.sheetnames[0]]
            letters = [(c.get("column") or "").strip() for c in plan["columns"]]
            if not any(letters):
                raise RuntimeError(
                    f"The mapping for sheet {name!r} has no spreadsheet columns recorded, so "
                    "nothing could be written into it. Re-upload the workbook on the ERP Entry "
                    "step so each heading is matched to its column."
                )
            header_row = plan["header_row"]
            # Clear the customer's own example rows first, or the ERP imports THEIR sample data
            # alongside this job's.
            if (ws.max_row or 0) > header_row:
                ws.delete_rows(header_row + 1, ws.max_row - header_row)
            # delete_rows clears cell CONTENT but leaves any merged_cells range whose anchor
            # sits at or above header_row and extends into the data area still declared -
            # Excel and openpyxl both then read every non-anchor cell in that range as
            # blank, no matter what gets written into it afterward. A customer template with
            # a title/banner cell merged down across the header and first data rows (an
            # ordinary real-world layout) silently swallowed whatever this wrote there, with
            # no error - exactly what "EVERYTHING HERE FAILS LOUDLY" above says must never
            # happen. Discard anything overlapping the rows about to be written into, so a
            # written value is actually readable, instead of hidden behind a stale merge.
            # ws.unmerge_cells() itself is not safe to call here: it also tries to delete
            # each non-anchor cell's entry from the worksheet's own cell store, which
            # delete_rows (just above) already removed - raising a KeyError on a range that
            # extends into the rows just deleted. Discarding the range declaration directly
            # needs no such cell bookkeeping, because those cells are already gone.
            for merged_range in list(ws.merged_cells.ranges):
                if merged_range.max_row > header_row:
                    ws.merged_cells.remove(merged_range)
            data = _rows_for_plan(plan, values, rows)
            for r, line in enumerate(data, start=header_row + 1):
                for letter, cell_value in zip(letters, line):
                    if letter:
                        _write(ws, r, xl.utils.column_index_from_string(letter), cell_value)
            written.append(f"{name}:{len(data)}")
        wb.save(str(target))
        logger.info("built %s from the customer's template (%s)", target.name, ", ".join(written))
        return target

    wb = xl.Workbook()
    first = True
    written = []
    for plan in live:
        title = (plan["sheet"] or "Sheet1")[:31]
        ws = wb.active if first else wb.create_sheet(title)
        if first:
            ws.title = title
            first = False
        header_row = plan["header_row"]
        for c, col in enumerate(plan["columns"], start=1):
            _write(ws, header_row, c, col.get("header") or col.get("field") or "")
        data = _rows_for_plan(plan, values, rows)
        for r, line in enumerate(data, start=header_row + 1):
            for c, cell_value in enumerate(line, start=1):
                _write(ws, r, c, cell_value)
        written.append(f"{title}:{len(data)}")
    wb.save(str(target))
    logger.info("built %s as a new workbook (%s)", target.name, ", ".join(written))
    return target


def describe(config: dict | None) -> str:
    """One line for the UI and the run log, so what will be produced is never a mystery."""
    if not config:
        return "no Excel configuration"
    plans = sheet_plans(config)
    src = "the customer's own template" if config.get("source") == "template" else "a new workbook"
    parts = []
    for p in plans:
        mapped = [c for c in p["columns"]
                  if (c.get("field") or "").strip() or _literals(c) is not None]
        if mapped:
            parts.append(f"{p['sheet'] or 'Sheet1'} ({len(mapped)} col, {p['scope']})")
    return f"{src}: " + (", ".join(parts) if parts else "nothing mapped")
