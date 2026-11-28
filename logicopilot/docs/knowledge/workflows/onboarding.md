---
title: Customer onboarding (Steps 0-6)
category: workflows
module: onboarding
owner: Senthil
status: ready
summary: How a Super Admin sets up a customer template in seven steps, with one owner per setting, straight on the live template.
---
# Customer onboarding (Steps 0-6)

## What it does
Customer Onboarding is the Super Admin's guided path for setting up one customer template
(for example "4S Air import") so that its jobs are read correctly and its ERP Excel comes out
the way the customer's ERP accepts it. It starts from two things the customer already has:
**their ERP import Excel** and **one sample of each document**.

The workflow follows one rule, agreed on 2026-11-26: **one owner per setting**. Every setting
is edited in exactly one step, and every step saves on the **live template** (the same data
jobs read). There is no separate onboarding copy that has to be synced later.

```
0 Choose Template ─► 1 ERP Field Mapping ─► 2 Document Mapping ─► 3 Prompts & Test
   ─► 4 Field Configuration ─► 5 Screen Designer ─► 6 Template Settings & Go Live
```

The step bar at the top of every onboarding page (`OnboardingSteps`) shows where you are.
The template chosen in Step 0 is carried through every step (in the page link and in the
browser tab), so it is never asked for again.

## How it works, step by step

### Step 0 - Choose Template
- Pick the customer template to work on.
- Upload the customer's **ERP Excel workbook**. Sheets and columns are read from the file
  (heading row found in the first rows, a few values under it used to suggest a data type).
  Nothing about a customer is hard-coded.
- Add the fields the **ERP UI script** picks on the ERP screen that are not in the Excel
  (ERP Quotation Name / Branch, ERP Importer Name / Branch, the ERP branch). They appear on a
  pseudo-sheet called **"ERP UI (not in Excel)"**.
- Upload **one sample per document** (Invoice, Packing List, Air Waybill ...).
- **Import current setup**: for a template that already runs on Excel entry, Steps 1, 2 and 4
  can be filled from its existing mapping instead of starting from nothing.

### Step 1 - ERP Field Mapping (classification)
Every column of the workbook is dragged into one of six value-source buckets:

| Bucket | Meaning |
|---|---|
| Document | Read directly off a source document (Invoice, Packing List, BL, AWB) |
| Reference | From a master / lookup table (Quotation Master, Importer Master, own table) |
| Fixed | A constant - no extraction, no calculation |
| Operator Input | Typed or picked by the operator during review |
| Calculated (AI-Derived) | Worked out by the AI from the documents and a rule |
| Calculated (Formula / Rule-Based) | Joined from other fields or decided by a rule |

- Each Document field is put on a document tab (a field can come from one document).
- Each field is **linked** to an existing live template field, or a new live field is
  **created** for it.
- By default only columns that hold a value in the uploaded Excel are shown - most of an ERP
  workbook is never filled.
- Every change saves as it is made; "Next" finishes the step once every column is classified.

### Step 2 - Document Mapping (WHERE a value sits)
- One sample per document. For each Document field, draw a **box** where its value sits.
- The value read inside the box is shown next to the ERP Excel's value, so a wrong box is
  seen at once.
- A box can record its **anchor** (the caption printed next to the value - it finds the value
  on other layouts) and can be ticked as a **line item** (draw on the first product row and
  every row is read).
- Only fields with a box drawn get read.

### Step 3 - Prompts & Test (HOW a value is read)
- The one place for reading prompts, label variations and format rules.
- **Generate missing prompts** builds a prompt for every box without one; a single box can be
  regenerated (the old prompt goes into the prompt history).
- Every box is read on the sample and compared with the customer's ERP Excel value:
  **To review** (no prompt / not tested), **Check** (value differs or nothing read),
  **Done** (matches, or no Excel value to compare).

### Step 4 - Field Configuration (HOW every field behaves)
One tab per value source; click a field to configure it.
- **Fixed** - the value, per-importer values, shown or hidden on the job screen.
- **Operator Input** - question, hint, input type (text / number / date / dropdown), choices,
  default, mandatory.
- **Reference** - Quotation Master, Importer Master or the field's own table; which field it is
  picked within; what it fills once picked; operator pick or automatic.
- **AI-Derived** - the AI rule and the documents it reads, with a Test button.
- **Formula** - the pieces a field joins, with a preview.
- **Document** - label, mandatory, input type, choices, validation, cross-document checks.
- The safe parameter rename lives here (moved from the Screen Designer).

### Step 5 - Screen Designer (layout only)
Lays out each document's job screen: sections and their order, where each field sits, and the
product card. Field behaviour is not edited here - that is Step 4.

### Step 6 - Template Settings & Go Live
Everything that belongs to the template as a whole: documents (name, required, order,
sample), template rules, mail intake and connected mailboxes, zip passwords, processing
options, how jobs reach the ERP and the ERP scripts, reference data (material master, fields
with their own table), cross-document checks, Combined Description, Tenant Admins of the
template, Data Transformation, classifier samples, prompt history.

Two tests run here before going live:
- **Test on the template samples** - reads each sample with the live settings.
- **End-to-end test** - reads every sample with the live prompts, adds fixed values and
  formulas, builds the ERP Excel exactly as a job does, and compares every column with the
  customer's own Excel. Nothing is saved. AI, reference and operator values are only filled on
  a real job.

**Auto ERP Submission** can be switched on here per template (and per branch), once the
customer-level switch allows it.

**Finish setup - make template live** runs a checklist first:

| Check | Level |
|---|---|
| The template has at least one document | Blocks |
| An Excel-entry template has at least one sheet mapped | Blocks |
| Every document has a sample | Warning |
| Every required document has boxes drawn | Warning |
| Every classified onboarding field is connected to a live field | Warning |
| A READY ERP script is linked to the template | Warning |
| Every box has a reading prompt | Warning |
| End-to-end test run in this session, and how many columns match | Warning |
| Operator questions have a hint | Warning |

A blocking item stops Go Live; warnings are shown and "Make live anyway" is allowed. Live
means the template status is **approved** - the only status operators see in New Job. "Take
off live" puts it back in testing; jobs already created are not affected.

## Business rules
- **One owner per setting.** Before this, the same setting was edited in two to five places and
  onboarding data never reached the live template, so configured values did not reach jobs.
- **Steps save on the live template.** Step 4 writes the onboarding field's configuration onto
  the live field it fills (`push_to_live`). On a template that is already live, the page asks
  once per session before saving, because running jobs change straight away.
- **A blank onboarding label never wipes a live label** (since 2026-11-28). Labels belong to the
  template; onboarding only adds or changes one.
- **Step 4 never blanks an AI rule** written in the field editor.
- **Classification is always the admin's choice**, never guessed from a column name.
  Suggestions (readable label, data type) are only suggestions.
- **A required document with no boxes is a warning, not a stop** (4S, 2026-11-26): the template
  may go live; nothing is read from that document until boxes are drawn.

## Where in the code
| Step | Frontend page | Backend |
|---|---|---|
| Step bar, carried template | `frontend/src/components/onboarding/OnboardingSteps.tsx` | - |
| 0 Choose Template | `pages/super-admin/OnboardingStartPage.tsx` | `api/v1/onboarding_erp.py` (`upload_workbook`, `set_header_row`, `erp_ui_from_script`), `core/erp_workbook.py`, `core/erp_ui_fields.py`; import: `api/v1/onboarding_erp_docs.py` `import_current`, `core/erp_import.py` |
| 1 ERP Field Mapping | `pages/super-admin/ErpFieldMappingPage.tsx`, `components/onboarding/LiveTemplatePanel.tsx`, `ErpUiPicker.tsx` | `api/v1/onboarding_erp.py` (`update_field`, `add_custom_field`, documents), `api/v1/onboarding_template.py` (`links`, `link_field`, `template_field`, `set_column_rule`, `set_sheet`) |
| 2 Document Mapping | `pages/super-admin/ErpDocumentMappingPage.tsx` | `api/v1/onboarding_erp_docs.py` (`doc_mapping`, `upload_sample`, `save_mark`, `set_line_item`), `api/v1/onboarding_template.py` `template_box` |
| 3 Prompts & Test | `pages/super-admin/OnboardingPromptsPage.tsx`, `components/onboarding/PromptsReview.tsx` | `api/v1/onboarding_template.py` (`generate-prompts`, `prompt_history`) |
| 4 Field Configuration | `pages/super-admin/ErpFieldConfigPage.tsx`, `components/onboarding/LiveBehaviourPanel.tsx`, `TemplateFieldEditor.tsx` | `core/erp_field_config.py`, `api/v1/onboarding_template.py` (`push_to_live`, `live_settings_from`) |
| 5 Screen Designer | `pages/super-admin/ScreenDesignerPage.tsx` | `api/v1/screen_designer.py` |
| 6 Settings & Go Live | `pages/super-admin/OnboardingTemplateSettingsPage.tsx` (`GoLiveCard`, `AutoErpSection`) | `api/v1/onboarding_template.py` (`end_to_end_test`, documents, users), template approval via `api/v1/reviews.py` |

Onboarding tables: `backend/app/models/erp_mapping.py` - `ErpWorkbook`, `ErpField`,
`ErpMappingHistory`, `ErpDocSample`, `ErpFieldMark`.

## Settings
- Template: `status` (approved = live), `entry_mode`, `excel_config`, `erp_column_rules`,
  `auto_extract`, `pull_email`, `pull_operator_id`, `excel_mode`, `large_zip_hold`,
  `large_job_lane`, `use_customs_tariff`, `prealert_shipments`, `importer_requires_gstin`.
- Onboarding field: `category`, `mandatory`, `display_label`, `config`, `ai_prompt`.

## Design decisions
- **Chosen:** the "Recommended Configuration Workflow" (2026-11-26) - seven steps, one owner
  per setting, one live data store. Built as phases A-G.
- **Rejected:** keeping a separate onboarding copy that is pushed to the template at the end -
  it was the cause of configured values never reaching jobs.
- **Kept as they are:** the older screens (Document Templates, Document Processing, Data
  Transformation, ERP Scripts, Email Puller, Zip Passwords). Step 6 edits the same settings;
  it does not replace those screens.
- Steps 2, 4, 6 and the field editor overlap with the job screen, which is Mageshwar's area -
  changes there should be agreed with him.
- Moving the job screen's hard-coded screen rules into template settings is designed but on
  hold; see the job screen page.
