---
title: ERP import Excel (building and editing)
category: modules
module: erp-excel
owner: Senthil
status: ready
summary: How a job's data becomes the ERP import workbook - sheets, columns, line items, invoice rows - and how the in-app ERP Excel editor replaces it.
---
# ERP import Excel (building and editing)

## What it does
Some ERPs (today SOFTLINK / LiveImpex for 4S) do not want fifty fields typed one by one - they
take a **bulk import workbook**. For a template set to Excel entry, Cargora builds that workbook
from the job's values, the ERP robot attaches it on the ERP's upload screen, and the ERP
imports it.

The same workbook is used for **Download as Excel**, for **GK2's Final Approve**, for the
**real ERP run** and for the **in-app ERP Excel editor**. There is one build, so they can never
disagree.

## How it works, step by step
1. **The template says what to build.** `TemplateGroup.entry_mode = "excel"` turns Excel entry
   on; `TemplateGroup.excel_config` describes the workbook (file name, source, sheets, columns).
2. **The job's values are collected** (`entry_values_and_rows`): one value per job-level field,
   and one value per product line for line fields. Each document's own reading is also kept
   under `label@@document`, so a column can read only from one document.
3. **The ERP column rules are applied to the config** (`erp_rules.effective_config`): a country
   column turns "POLAND" into "PL", a currency column turns "US$" into "USD" - only when certain.
4. **Auto ERP only:** empty mandatory values are filled with the best guess for the workbook
   (`auto_erp_complete.complete_for_erp`); the job keeps what was read and the job note lists
   every filled value.
5. **The pre-ERP check runs** when the workbook is going to the ERP (`erp_rules.pre_check`). A
   value the ERP would refuse stops the job here with a Fix card. Downloads and the editor still
   build, so the operator can see what is wrong. See the ERP rules page.
6. **An empty workbook is refused**: if every mapped column is empty, the build stops with a
   message instead of sending nothing.
7. **The workbook is written** (`build_for_job`), one sheet per plan, and named after the job
   reference.

### Two kinds of workbook
| Source | What happens |
|---|---|
| `template` | The customer's own workbook (the one the ERP already accepts) is opened. Its example rows are cleared and each sheet is written under its own header row. The layout the ERP matches on survives. |
| `blank` | A new workbook is written; column order is whatever the mapping says. |

### How many rows a sheet gets (scope)
| Scope | Rows | Example |
|---|---|---|
| `job` | Exactly one | GENERAL, SHIPMENT |
| `line` | One per product line (zero if the job has no lines) | ITEMS |
| `invoice` | One per invoice on the job | INVOICES |
| `fixed` | As many as the longest literal list in the mapping | STATEMENT, EXCHANGE_RATE |

A job-level field placed on a line sheet repeats down every row (the BE number belongs on every
line). On an invoice sheet, each row takes that invoice's own value.

### Where a column's value comes from
Each column in a sheet plan can carry:
- **`field`** - the template field it reads.
- **`from_document`** - read the field only from that document (e.g. pieces and gross weight
  only from the Air Waybill, never the invoice's "0").
- **`pick_by`** - another field chooses which field to read (e.g. the operator ticks whether the
  supplier is the shipper or the Sold By party).
- **`fallback_fields`** and a literal - a chain: the field first, then each fallback field, and
  the literal only as the last resort.
- **`value` / `values`** - a constant for every row, or one per row. A literal can contain
  `[[field]]` to insert a field's value.
- **Serials** - `#row` (ItemSrNo / InvSrNo) and `#invoice` (which invoice a line belongs to).
- **`transform`** - `leading_digits`, `code_in_brackets`, `sea_port_code` (column editor), or
  `country_iso2`, `currency_code`, `upper` (set only by ERP column rules).
- **`ai_map`** - map a value to a code from a list of options.
- **`max_len`** - cut the text to the column's width (e.g. General_Description takes 60
  characters).

### How cells are written
- Everything is written as **text**: leading zeros ("007") and container numbers must not turn
  into numbers.
- A value starting with `=`, `+`, `-` or `@` is written as text, never as a formula.
- An empty value leaves the cell **truly empty**. An empty-string cell made the ERP refuse the
  file ("Entry should be of 2 characters" on a column meant to be blank).

### Invoice of each line
Documents are paired on their invoice numbers and every line carries the invoice set it came
from, so InvSrNo on ITEMS is correct on multi-invoice jobs. With nothing to say otherwise, a line
belongs to invoice 1 - never a guess.

## The ERP Excel editor
- On the job screen, the operator can open the job's ERP workbook in the app
  (`ErpExcelEditor`): every sheet as a grid, editable like a spreadsheet (range select, drag to
  fill, number series, copy / paste with real Excel, undo / redo).
- The header row is locked (the ERP maps columns by it); a sheet only has the columns its
  headers define.
- **Save** keeps the edited copy as the job's workbook. From then on Download and the real ERP
  import use it, until **Reset to generated**.
- Field corrections made after the save are **not** in the edited copy until it is reset; the
  screen says so.
- The workbook cannot be changed while the ERP entry is running, or once the ERP has the job.
- Each save and reset is recorded in the job history.
- While an edited copy exists, the pre-ERP rule check is skipped (the hand-edited file replaces
  the generated one).

## Business rules
- **Everything fails loudly.** A wrong figure in a customs import is worse than a job that
  stops, so a missing template file, a renamed sheet or an unmapped workbook raises an error
  instead of producing a plausible-looking file.
- **Renaming a field updates the Excel mapping** (`rename_field_in_excel_config`), otherwise that
  column would silently go out empty on every job.
- **Download is always rebuilt fresh** from the job's current data, never a cached copy.

## Where in the code
| Area | Code |
|---|---|
| Building the workbook | `backend/app/core/excel_entry.py` - `sheet_plans`, `_rows_for_plan`, `_picked_field`, `_literals`, `invoice_of`, `_write`, `build_for_job`, `read_headers`, `describe` |
| Job values for the build | `backend/app/api/v1/jobs.py` - `entry_values_and_rows`, `_build_job_excel` |
| Download / editor endpoints | `backend/app/api/v1/jobs.py` - `download_job_excel`, `open_job_excel`, `save_job_excel`, `reset_job_excel` |
| Edited copy | `backend/app/core/erp_excel_edit.py` - `edited_file`, `locked_reason`, `use_edited_or_build`, `read_sheets`, `save_sheets`, `reset` |
| Rule conversions | `backend/app/core/erp_rules.py` - `effective_config` |
| Auto ERP fill | `backend/app/core/auto_erp_complete.py` - `complete_for_erp` |
| Editor screen | `frontend/src/pages/jobs/ErpExcelEditor.tsx` |
| Mapping the sheets | Template wizard ERP Entry step (`frontend/src/pages/super-admin/template-wizard/TemplateCreationWizard.tsx`) and onboarding Step 1 Live template panel (`frontend/src/components/onboarding/LiveTemplatePanel.tsx`) |

## Settings
- Template: `entry_mode`, `excel_config` (inside it: `source`, `file_name`, `sheets`, `scope`,
  `header_row`, `columns`), `erp_column_rules`.

## Design decisions
- **One build for every use** (download, GK2 approval, ERP run, editor) so a fix can never land
  on only one path.
- **The edited copy wins until reset** (agreed 2026-11-19); no database column - the file
  existing is the switch.
- **Rule conversions are kept out of the column editor's list** (`_RULE_TRANSFORMS` separate from
  `_TRANSFORMS`), because the column editor validates against its own list.
