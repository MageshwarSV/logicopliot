---
title: ERP field configuration and pre-ERP checks
category: business-logic
module: erp-rules
owner: Senthil
status: ready
summary: What each ERP Excel column accepts, how it is checked before the ERP, how mandatory and allowed values are set, and how ERP refusals are explained.
---
# ERP field configuration and pre-ERP checks

## What it does
The ERP refuses an import for small reasons: a country written in full instead of "PL", a
currency with 4 characters, an empty mandatory column. Each refusal used to cost a failed job
and a phone call. This module makes Cargora check **every column of the ERP Excel before it is
sent**, so a value the ERP would refuse is fixed in Cargora first.

It has four parts:
1. **ERP Field Configuration** (Super Admin page) - per template, every ERP Excel column: which
   document and field feed it, whether it is mandatory, its rule, and whether the mapping works.
2. **ERP column rules** - what each column accepts, plus rules built in without setup.
3. **The pre-ERP check** - runs before the ERP, and live on the job screen during review.
4. **ERP rejection guidance** - when the ERP still refuses, it is explained in plain words.

## How it works, step by step

### 1. Rules belong to the ERP column
A rule is kept per **ERP column** (sheet + header, e.g. `ITEMS|Country_of_Origin`), not per
Cargora box. One box can feed two columns, and each column's own rule applies. Rules are stored
per template in `erp_column_rules`. A rule can say:

| Part | Meaning |
|---|---|
| `required` | Must not be empty (the one meaning of "mandatory") |
| `length` + `length_mode` | Exactly or at most N characters |
| `length_when` | Length depends on another column (e.g. 3 for Air, 5 for Sea by TransportModeCode) |
| `allowed` | Only these values |
| `master` | Must be a country, currency or unit (UQC) code |
| `number` | Must be a number |
| `pattern` + `message` | Must match a format; the message is shown when it does not |
| `uppercase` | Sent in capitals |
| `off` | Switches off the built-in rule for this column |

### 2. Built-in rules (no setup)
- A column whose header names a **country** (Country_of_Origin, CountryOfOriginCode,
  MFG_Country ...) must be a 2-letter ISO code. Headers that also say certificate, percent,
  amount, name or address are left out.
- A column whose header names a **currency** must be a 3-letter ISO code.
- **PortOfShipmentCode** must be 3 characters for Air and 5 for Sea.
- A stored rule for the column replaces the built-in one; `off` removes it.

### 3. Conversions when certain (currency rule, country rule)
Before the workbook is built, a country column converts a country name to its code ("POLAND" ->
"PL") and a currency column converts a symbol or name ("US$" -> "USD", "Rs." -> "INR") - **only
when the match is certain** (an exact name or a well-known alias). Anything else is left as it
is, so the check reports it for a person to fix. The conversion applies per column: the same
value feeding another column is untouched.

On the job screen, a box that feeds a country or currency column becomes a **search dropdown
that saves the code**, and saving checks it. A box with its own AI-aided condition keeps that
behaviour instead.

### 4. The pre-ERP check (stop before ERP)
- Every cell of the workbook, as it would be built, is checked against the column rules. The
  source field's own job-screen validation pattern is also applied to the value it sends,
  including on product lines.
- Runs when the job is about to go to the ERP (GK2 approval, Final Submit, Submit Entry).
- If anything fails, the job **stops before the ERP**: ERP status failed, a summary ("Not sent to
  the ERP - it would refuse these values ..."), and the **Fix card** with one row per box.
- **Auto ERP jobs** are checked on the values the workbook will really carry, after empty
  mandatory values have been filled.
- The same check runs **live during review** (`GET /jobs/{id}/erp-check`): the job screen shows
  what the ERP would refuse on Required Details Review and ERP Submission, re-checked after every
  save.
- The check is skipped for a template that is not Excel entry, or when the job has a hand-edited
  ERP Excel. A rule with a broken pattern never blocks a job.

### 5. ERP Field Configuration page
- Pick a template. Two views: **By document** (default - each field as the operator sees it,
  grouped by document, with every sheet / column it feeds) and **By column** (every ERP column,
  sheet by sheet).
- Each column has a status: **Configured** (has a working source), **Missing** (mandatory with no
  source - always goes out empty), **Invalid** (the source does not work: field not on the
  template, unknown document or conversion, broken pattern, text cut at a length limit),
  **Not used** (no source, not mandatory - most columns).
- **Mandatory** has one meaning: the column's `required` rule. Other signals are only
  **suggestions**, each with its reason:
  - the field is "Ask operator + required" on the job screen;
  - the ERP refused this column as empty (last 90 days of failure reports and rejections);
  - the onboarding ERP field list marks it mandatory;
  - **Analyse recent jobs**: the column had a value on at least 98% of jobs the ERP accepted
    (measured on at least 10 jobs).
- **Analyse** reads up to 80 recent accepted jobs and stores, per column, the fill rate and - for
  a column with at most 20 distinct values - the values the ERP accepted. Those can be offered as
  the column's **allowed values** in one click. Analyse never changes a rule by itself.
- Columns can be marked mandatory in bulk. Rules can be **copied from another template** and
  **tested on a job** by its reference.

### 6. Rules suggested from ERP rejections
When the ERP refuses an import, Cargora reads its wording and remembers a suggested rule per
refused column: "should be of 2 characters" -> exact length 2; "mandatory / cannot be blank" ->
required; "not found / invalid" -> needs a list of allowed values (only a person can give it).
A suggestion already covered by the current rule is not shown. The Super Admin accepts or
dismisses each on the ERP Field Configuration page; a dismissed one does not come back.

### 7. ERP rejection guidance (when the ERP still refuses)
- **Excel import refused** ("Invalid Data Found" table): every row is read by code - sheet, ERP
  column, invoice no., product no., rule. Each column is traced through the Excel mapping to the
  Cargora box that fed it (never guessed). Duplicates are merged (two columns from one box, the
  same box on many products, are one fix). One short AI call writes what the ERP wants in plain
  English with an example; without the AI, the ERP's own rule is shown. A column no box feeds is
  marked as needing Cargora support.
- **ERP screen refused a value** (a dropdown that kept clearing): the card names the box, what
  was sent, and says to pick a value the ERP already has. For a master-data box (ERP Quotation /
  Importer Branch and address) it points to **Tenant Admin -> ERP Master Data**, spelled exactly
  as the ERP's list spells it, instead of "add it in the ERP".

## Business rules
- **Stop before the ERP** when a value breaks a rule and cannot be converted with certainty -
  Auto ERP included - because the ERP would refuse it anyway. (Default chosen 2026-11-27.)
- **Auto ERP keeps filling empty mandatory values** instead of stopping (decided 2026-11-28).
- **Convert only when certain**; never guess a country or currency.
- **Rules are kept by the Super Admin per template**, with copy-from-template.
- **The ERP robot and ERP scripts are not changed** by this work; checks happen before the run.

## Where in the code
| Area | Code |
|---|---|
| Rules, conversions, check | `backend/app/core/erp_rules.py` - `builtin_rule`, `effective_rules`, `effective_config`, `validate`, `check`, `field_patterns`, `pre_check`, `summary`, `infer_rule`, `record_suggestions`, `master_by_label`, `code_for_saving` |
| Code lists | `backend/app/core/erp_masters.py` - countries, currencies, UQC, `normalize_country`, `normalize_currency` |
| Configuration view, Analyse | `backend/app/core/erp_field_map.py` - `config_rows`, `field_rows`, `refused_counts`, `analyse` |
| Endpoints | `backend/app/api/v1/erp_rules.py` - `columns`, `save`, `copy_from`, `check_job`, `accept_suggestion`, `dismiss_suggestion`, `field_config`, `analyse_jobs`, `set_mandatory`, `field_view` |
| Where the check runs | `backend/app/api/v1/jobs.py` - `erp_rule_check`, `_build_job_excel`, `complete_job` |
| Rejection guidance | `backend/app/core/erp_rejection.py` - `parse_rows`, `build_items`, `explain`, `build_help`, `build_field_help` |
| Super Admin page | `frontend/src/pages/super-admin/ErpColumnRulesPage.tsx`, `ErpFieldsByDocument.tsx` (route `/super-admin/erp-column-rules`) |
| Job screen | `frontend/src/pages/jobs/ErpFixCard.tsx`, `LiveErpCheck.tsx` |

## Settings
- Template: `erp_column_rules` (with its internal keys `_suggested`, `_dismissed`, `_analysis`),
  `entry_mode`, `excel_config`.
- Field: `validation_pattern`, `validation_message`, `ai_aided_prompt`.

## Design decisions
- **Rules on the ERP column, not the box** - one box can feed two columns with different rules.
- **One meaning of mandatory** (2026-11-28) - several flags used to mean "mandatory"; now only the
  column's `required` rule does, and the others are suggestions with reasons.
- **Rule conversions are separate from the column editor's conversions**, so the column editor
  (Mageshwar's area) is not affected.
- **Production dry run** (2026-11-27): 285 Excel jobs over 14 days - none would have been stopped;
  currency columns held only valid codes or blanks.
