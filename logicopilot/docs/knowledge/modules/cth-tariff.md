---
title: Customs tariff (CTH) - ICEGATE master, dump and AI suggestion
category: modules
module: cth-tariff
owner: Senthil
status: ready
summary: How each product line gets its 8-digit CTH - the customer's CTH dump first, the global Indian Customs Tariff as the list of allowed codes, and an AI suggestion when the dump has nothing.
---
# Customs tariff (CTH)

## What it does
Every product line on an import job needs an 8-digit **CTH** (Customs Tariff Heading - also printed
as HS Code, HSN, HTS, RITC, ITC-HS ...). Cargora has two sources for it:

1. **The customer's Reference CTH Dump** - their own material master workbook (part code /
   description → CTH), one per template. This lookup is unchanged and always runs first.
2. **The Indian Customs Tariff (ICEGATE)** - one global list of every allowed 8-digit code, for all
   tenants and all streams. It decides which codes are **allowed at all**. When the dump has
   nothing usable for a line, Cargora suggests the nearest tariff code with the AI.

The rule behind it: **no CTH outside the ICEGATE tariff may be picked or sent to an ERP.**

## How it works
### Loading the tariff (Super Admin)
1. Super Admin → **Customs Tariff** (`/super-admin/customs-tariff`) uploads the full list (Excel or
   CSV). Columns are found by header: the code ("CTH", "Tariff Item", "HS Code" ...), and optionally
   Rate, Unit, Preferential Rate and Description.
2. **Always two steps:** a preview shows what would change (added / changed / missing), then apply
   writes it in one transaction.
3. A file with far fewer codes than a full tariff (under 1,000) is refused - applying it would mark
   almost the whole master inactive. A full tariff is about 12,700 codes.
4. A code missing from a new file is marked **inactive, never deleted**, so old jobs still show it.
5. Codes are cleaned: `0101 21 00`, `0101.21.00` and `01012100` are the same. A 7-digit *number*
   cell gets its lost leading zero back; a 7-digit *text* is refused.
6. Every upload is kept as a version (`customs_tariff_uploads`).
7. The page also has a **Try** box (run the matching engine on one line, nothing remembered) and a
   **dump check** (for every template's dump: how many of its codes are not in the tariff).
8. Tenant Admins see the same list read-only (Master Data → Customs Tariff).

### On a job (template switch on)
After extraction and the dump lookup (`apply_to_job`, called from `jobs.py`), for the template's CTH
fields ("Dump CTH Number", "CTH"):
- a value that **is** an active tariff code stays;
- a value **not** in the tariff (old or wrong dump code, "NA", a previous entry) is **not used** -
  emptied, with a note saying so;
- an **empty line** gets the nearest tariff code from the matching engine. It goes in the **CTH
  answer box**, labelled "AI suggestion from the Indian Customs Tariff - not from the dump". The
  "Dump CTH Number" box only ever shows what the dump really gave.

The operator may change the suggestion; a typed or picked code must be in the tariff
(`check_correction`). No tick is needed - the suggestion goes as it is. **Submit is blocked only by a
CTH outside the tariff** (`submit_problems`). The tariff search sits on the CTH box.

### The matching engine (`customs_tariff_match.py`)
For each line (document CTH, part code, description):
1. **Document CTH is an active tariff code** → used as it is, no AI.
2. **Answered before** for this tenant, same wording → the remembered code (table
   `cth_tariff_suggestions`, kept apart from operators' "Previous entry" store - it is the AI's
   answer, not a person's).
3. **AI, two steps** (the tariff file has no descriptions):
   a. a short list - the codes under the document CTH's first 6 (or 4) digits; with no usable
      document code the AI first guesses the 8-digit code from the description, and an exact
      active hit is the answer, otherwise its first 6 (or 4) digits give the list;
   b. the AI picks **one** code from that list (each shown with unit and duty rate). An answer
      outside the list is never used.
4. **AI failed or answered outside the list** → the subheading's residual "Other" code (ending
   90 / 99 / 00), so Auto ERP always has an allowed code.
5. Nothing to go on (no code, part or words) → empty.

Lines are sent to the AI in batches of 40 (one call per step per batch), using the same model as
extraction.

### Auto ERP and the ERP Excel
- On an Auto ERP job, `auto_apply` puts each line's tariff code into the CTH answer box as the
  extracted value, without a person's tick, and lists it in the job note.
- In the ERP Excel a CTH column only ever carries a tariff code (`erp_value`): never "NA", never a
  code outside the tariff (that cell is emptied instead).

## CTH Master data page (the customer's dump)
Tenant Admin → Master Data → **CTH Master** (`/tenant-admin/master-data/cth`) shows the customer's
dump per template: list, search and sort rows (Part Code, Description, CTH), **edit one row**, add
one row, or add a small batch from the 3-column template. Download is available too.
- The workbook **is** the data - no database copy. Edits rewrite the workbook (all original columns
  kept), swap it in atomically and rebuild the jobs' lookup; the previous file goes to backups
  (last 10 kept).
- Rows are read from a cached copy built in the background, because a large sheet takes about 30
  seconds to open.
- A **full replacement** of the dump is not done here - it stays a Super Admin job (template upload).

## Business rules
| Rule | Why |
|---|---|
| Dump lookup runs first and is unchanged | Each customer's dump is their own trusted data |
| Dump codes not in the tariff are **not** used; the AI suggests instead | Agreed 2026-11-26 - an invalid code would be refused at customs/ERP |
| Previous-entry codes not in the tariff are skipped | Same rule: only allowed codes |
| A valid document CTH is used directly, no AI | It is already right and allowed; saves AI cost |
| AI must pick from the tariff list; outside answers are dropped | The AI can invent codes |
| Suggestion sits in the CTH box, no tick needed (changed 2026-11-26) | Replaced the earlier "operator must tick" design to save operator clicks |
| Codes are never deleted from the tariff | Old jobs keep showing what their code is |
| Everything off unless the template switch is on | Safe rollout; rollback = switch off |

## Where in the code
| What | Where |
|---|---|
| Tariff load / search / check | `backend/app/core/customs_tariff.py` - `parse_file`, `preview_upload`, `apply_upload`, `active_codes`, `is_allowed`, `clean_cth`, `search` |
| Matching engine | `core/customs_tariff_match.py` - `suggest_many`, `suggest_one`, `candidates_for`, `residual`, `dump_check` |
| On jobs | `core/customs_tariff_jobs.py` - `apply_to_job`, `check_correction`, `submit_problems`, `auto_apply`, `erp_value`, `tariff_on` |
| Caption names for a CTH field | `core/tariff_codes.py` - `TARIFF_CODE_CAPTIONS`, `is_tariff_code_field` |
| Customer dump page | `core/cth_master_rows.py` (`update_row`, `add_rows`, `read_add_file`), `api/v1/master_data_cth.py` |
| Tariff API | `api/v1/customs_tariff.py` (upload, preview, codes, try, dump-check) |
| Models | `models/customs_tariff.py` - `CustomsTariff`, `CustomsTariffUpload`, `CthTariffSuggestion` |
| Screens | `frontend/src/pages/super-admin/CustomsTariffPage.tsx`, `frontend/src/pages/tenant-admin/CthMasterDataPage.tsx` |

## Settings
- Template: `use_customs_tariff` (Onboarding → template settings, "CTH only from the Indian Customs Tariff (ICEGATE)")

## Design decisions
- **Global master, not per customer** - customs codes are the same for everyone.
- **Option B** (2026-11-26): the first tariff file has no descriptions, so the AI guesses the code
  and then chooses from the codes under that prefix. Descriptions can be added later by
  re-uploading a file with a Description column.
- **Remember AI answers per tenant** so the same line never costs a second AI call and always gets
  the same code.
- **Fallback to the "Other" code** rather than leaving an Auto ERP line empty.
