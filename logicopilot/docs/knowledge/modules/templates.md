---
title: Templates & fields
category: modules
module: templates
owner: Senthil
status: ready
summary: What a template is - mode, document slots, fields, display labels, template-wide settings, renaming and the naming standard - and why template configuration is set per environment.
---
# Templates & fields

## What it does
A **template** (in the code: a *template group*) is the full recipe for one customer workflow.
It says:
- which documents a job of this kind has;
- which values to read from each document;
- how each value behaves on screen;
- how the job reaches the ERP.

Every job belongs to exactly one template. Cargora is one engine configured per customer, and
the template is where most of that configuration lives. That is why behaviour belongs in the
template, not in code.

## The parts of a template
| Part | What it is | Table |
|---|---|---|
| **Template group** | The template itself: name, tenant, mode, status (`draft` / `ready`), template-wide settings | `template_groups` |
| **Mode** | The transport mode, e.g. Air Import, Sea Import, Air Export, Sea Export (full list in `core/modes.py`). Picks the classifier module and is checked against the tenant's allowed modes | `template_groups.mode` |
| **Document slot** | One document of the template: Invoice, Packing List, Air Waybill, Freight Certificate … - with a name, document type, order, *required* flag, *manual entry when missing* flag and a sample file | `template_documents` |
| **Field (box)** | A value read from one document (a box drawn on the sample). Has an internal `label_name`, prompt, caption variations, format hint, multi-value (product line) flag, validation, dropdown options, width and order | `field_marks` |
| **Computed field** | A value not boxed on a document: `hardcoded`, `ai`, `lookup` or `composite` | `custom_fields` |
| **Cross-document check** | Two fields that must hold the same value | `cross_doc_links` |
| **Reference values** | Lookup tables a field reads (ports, importer branches …) and learned answers | `custom_field_reference_values` |

## How it works
1. **Create.** A Super Admin creates a template for a tenant and picks its mode
   (`template_groups` create endpoint), or copies an existing one (`duplicate_group`) - in the
   same tenant or into a new tenant.
2. **Onboard.** Customer Onboarding Steps 0-6 fill it in:
   - ERP field mapping;
   - document mapping (boxes);
   - prompts and testing;
   - field configuration;
   - Screen Designer (layout);
   - Template Settings & Go Live.
   Each setting has one owner step (see the onboarding pages).
3. **Make live.** Finalising sets the status to `ready`, which makes the template usable for jobs.
4. **Route mail.** A mailbox on the Email Puller screen is routed to the template, so its mail
   becomes jobs of this template.
5. **Run.** Every job uses the template's slots for [classification](classification.md), its
   fields for [extraction](extraction.md), and its labels and layout on the job screen.

## Display labels - the only source of field names on screen
- `label_name` is the field's internal key. The Excel builder, ERP rules, ERP robot and built-in
  behaviours all work on it. A label never changes it.
- `display_label` (on `field_marks` and `custom_fields`) is what the operator sees. The job screen
  shows `display_label`, or the bare `label_name` if no label is set (`fieldLabelOverride` in
  `JobRunPage.tsx`).
- Labels are set in onboarding or Screen Designer. **Never hardcode a label in code, and never
  take labels from an Excel file.** The old hardcoded label lists were copied into each
  template's `display_label` and removed from the code on 2026-11-28
  (`deploy/oneoff/move_ui_labels_to_template.py`).
- Some `label_name` values switch on built-in behaviour (quotation fields, CTH lookup, GSTIN
  rule, insurance, ports …). Template Settings lists them under *Built-in field names*. Renaming
  one turns that behaviour off for the template.

## Template settings (template-wide)
Set on Template Settings (Step 6) or on Document Processing:
- **Documents:** name, type, required, manual entry, order, sample.
- **Mail intake:** routed mailboxes, zip passwords.
- **Processing:** `auto_extract`, `large_zip_hold`, `large_zip_mb`, `large_job_lane`,
  `excel_mode`, `excel_ignore_patterns`.
- **Air pre-alerts:** `prealert_shipments`.
- **Importer:** `importer_requires_gstin`.
- **Customs:** `use_customs_tariff` (the tariff must be uploaded first).
- **ERP:** `entry_mode` (`fields` or `excel`), `excel_config`, `erp_column_rules`, ERP scripts,
  Auto ERP Submission.
- **Rules:** `ruling_prompt` (which documents a job really needs), cross-document checks,
  Combined Description, material master, reference tables.
- **Classifier samples** and **prompt history**.

## Rename and the naming standard
- **Rename** (Template List > Rename, `rename_group`; Super Admin or the tenant's own Tenant
  Admin) changes only the name. Jobs, fields, scripts and assignments point to the template by
  id, so nothing moves and job history is untouched.
- The server refuses an empty name, and refuses a name another template of **the same customer**
  already uses (not case-sensitive). Two templates with the same name could not be told apart in
  user assignment, mailbox routing or reports.
- **Naming standard** (checked as you type, advice only):
  - start with the shipment mode, e.g. `Air Import`, `Sea Export`;
  - add `– what makes it different` only when the customer has more than one template of that
    mode, e.g. `Sea Import – Excel`, `Air Import – Bangalore`;
  - Title Case, 40 characters or fewer;
  - no dates, no customer name (it already shows in the Tenant column), no "copy / test / new /
    final / (excel)".

## Per-environment configuration
Template configuration lives in the **database**, not in git. A code deploy never changes a
template. Templates, fields, labels, prompts, reference data and template settings are changed
through the admin screens / API **on each environment separately** (SIT, then production when
agreed). A finished template can be moved from one server to another with the one-off
`deploy/oneoff/template_bundle.py`. It never overwrites an existing template, and jobs, users,
mailboxes and correction history do not travel. Copying a template (`duplicate_group`) never
copies the mail routing, the material master, ERP scripts or learned reference values.

## Business rules
| Rule | Why |
|---|---|
| Labels only from the template | One engine for every customer; a label change must not need a code change |
| `label_name` never changes because of a label | Excel, ERP rules and the robot key on it |
| Template names unique per customer | Lists and routing must be unambiguous |
| A job's mode must be one the tenant is allowed | A job filed under an unlicensed mode is held, like an unrecognised customer |
| Processing settings are Super Admin only | They change cost and behaviour of every job of the template |
| `use_customs_tariff` needs the tariff uploaded first | Otherwise every CTH would be empty |
| A copy never takes the original's mailbox | It would steal the original's incoming mail |

## Where in the code
| What | Where |
|---|---|
| Models | `backend/app/models/template_group.py`, `template_document.py`, `field_mark.py`, `custom_field.py`, `cross_doc_link.py`, `custom_field_reference.py` |
| Modes | `core/modes.py`; classifier picker `core/classifier_common.classifier_module` |
| Template API | `api/v1/template_groups.py` - `rename_group`, `set_group_mode`, `set_group_processing_settings`, `set_group_prealert_shipments`, `finalize_group`, `duplicate_group` |
| Slot API | `api/v1/template_documents.py` (upload sample, required, manual entry, name, doc type) |
| Field API | `api/v1/field_marks.py` (marks, custom fields, cross-doc links, reference values) |
| Onboarding | `api/v1/onboarding_template.py` (`push_to_live`), `api/v1/screen_designer.py` |
| Screens | `pages/super-admin/TemplateListPage.tsx` (rename + naming standard), `OnboardingTemplateSettingsPage.tsx` (Step 6), `ScreenDesignerPage.tsx`, `components/onboarding/TemplateFieldEditor.tsx` |
| Moving a template between servers | `deploy/oneoff/template_bundle.py` |

## Settings
All per template, as listed under *Template settings* above. There is no server setting for templates.

## Design decisions
- **Configurable, not hardcoded** - new customers should be onboarded by configuration, not by
  code changes.
- **Rename by id** - the name is only a label, so templates built under a working name can take
  the customer's real name later.
- **Database config per environment** - SIT can be changed and tested freely without touching
  production's live templates.
- The remaining hardcoded job-screen rules (section order, widths, some dropdowns) are planned to
  move into template settings. This is on hold and agreed with the job-screen owner first.
