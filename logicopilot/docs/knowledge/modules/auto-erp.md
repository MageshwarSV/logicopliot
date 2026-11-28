---
title: Auto ERP Submission
category: modules
module: auto-erp
owner: Senthil
status: ready
summary: Jobs that go straight to the ERP after extraction with no review - who switches it on, which jobs qualify, how empty values are filled, and what can still stop a job.
---
# Auto ERP Submission

## What it does
Normally an operator (Gate Keeper 1) reviews every job before it goes to the ERP. With **Auto ERP
Submission** switched on for a template (and optionally only for some branches), a job is sent to
the ERP **the moment extraction finishes** - no review, no approval in Cargora. The customer's own
team corrects anything wrong inside the ERP afterwards.

It was asked for by 4S Logistics. Today it is used for Bangalore jobs on 4S templates.

## How it works
1. **Extraction finishes.** The job becomes "extracted" and `safe_maybe_auto_submit` is called
   (also when a person approves a job flagged as a possible duplicate).
2. **Is it switched on?** `rule_for` checks the tenant's `auto_erp_allowed` and the template's
   rule in `auto_erp_rules`. Either off → nothing happens, the job waits for review as usual.
3. **Never twice.** A job already sent (`auto_erp_submitted`, or already submitted to the ERP by
   anyone) is not sent again - a re-extraction stays with the people.
4. **Can anything reach the ERP?** The template must have a `ready` ERP script and use **Excel
   entry**. If not, the job is **held** for manual review and the reason is written in the job's
   Auto ERP note.
5. **Branch.** If the ERP script has a branch field, the job's value in that field is compared to
   the rule's branch list (`branch_matches`). No branches listed = every branch. A job for another
   branch goes through the normal manual review.
6. **Empty choices are filled** (see below), and tariff codes are applied when the template uses
   the customs tariff (`customs_tariff_jobs.auto_apply`).
7. **Sent.** The job is marked `auto_erp_submitted`, GK2 is skipped, a note is written on the job
   and in its history, and `_kick_off_real_erp_submission` puts it on the same ERP queue a manual
   submit uses (same robot, branch logins, failure banner and Retry - see the ERP robot page).
8. **Workbook build.** When the ERP Excel is built for an auto job, `complete_for_erp` makes sure
   no mandatory column goes out empty, and the filled values are added to the note.

## Filling empty values
**Required operator-pick fields** (`fill_operator_defaults`, `auto_erp_match.py`):
- **Importer name**: the importer name on the documents matched against the Importer master.
- **Billing customer** (ERP Quotation Name): matched from the forwarder / quotation name on the
  documents.
- **A master's branch** (e.g. importer address branch): among that name's own branches, the one
  that names the job's branch, else the AI picks from the consignee address, else the first.
- **Anything else**: the first real option of its list (never a junk row like "."), else the
  field's first fixed choice.
- A field with nothing to choose from stays blank and the job still goes.

**Every dropdown field on the job** (`fill_job_values`, header and product lines) and **every
mandatory ERP column** (`complete_for_erp`), in this order:
1. a valid value stays;
2. a value not in the list → the nearest choice (PCS / PC / PIECES / NOS, KG / KGS ...);
3. empty on a product line → what the job's other lines use, else the job's quantity unit;
4. the value this template's completed jobs use most for that field;
5. the first choice.

A required column with no list gets `NA` for text and `0` for numbers - except a **CTH** column on
a tariff template, which only ever carries a tariff code (never `NA`, never a code outside the
tariff). Values are saved as the *extracted* value, never as a correction, so the screen still
shows they were not a person's choice. A person's correction is always kept.

**Why:** found on production (JOB-E739B0, 2026-11-25) - the product-line unit was empty on all 16
lines and the ERP refused the whole import. Since nobody reviews an auto job, Cargora fills it and
lists what it filled so the customer can check it in the ERP.

## Business rules
- **Default OFF everywhere.** A tenant or template nobody configured keeps the manual flow.
- **Nothing in Cargora blocks an auto job** (agreed 2026-11-25): cross-document rule failures, a
  missing document, wrong or incomplete values - it goes anyway; the note lists missing documents
  ("Sent without: ...") and every value picked automatically.
- **Decision 2026-11-28: keep filling.** Empty mandatory values are filled, not used to stop the job.
- **What it will not do:** send a job flagged as a possible duplicate (would be a second ERP entry),
  send the same job twice, or send when there is no ready ERP script / no Excel entry.
- **ERP column rules still apply.** The pre-ERP check (`erp_rules.pre_check`) runs on auto jobs too:
  a value the ERP would certainly refuse and that cannot be converted stops the job with a Fix card,
  because the ERP would refuse it anyway.
- **Verified** stays a manual step, as for every submitted job.
- **The robot's AI** may take over anywhere on an auto job (on manual jobs only on marked steps),
  because nobody is watching it.
- A crash inside Auto ERP never breaks extraction: the job stays in Ready for Review with the
  reason in its note.

## Who is shown as owner
In Production Support (`api/v1/support.py`, `SupportJobsPage.tsx`) an auto job still shows its
**owner** - the operator it is assigned to, else who created it - with a **"Auto ERP"** tag. Only a
job with no owner shows "Auto ERP" as the user. The Support overview counts auto-submitted and
manually submitted jobs separately. On the job screen (`JobRunPage.tsx`) the Auto ERP note shows
what was sent, picked or held.

## Failures
An auto job that fails in the ERP behaves like any failed job: "Failed" with the ERP's reason (and
the plain-English fix list for "Invalid Data Found"). Because GK2 was skipped, an operator can fix
the values and press **Retry** directly.

## Where in the code
| What | Where |
|---|---|
| Switch, rules, hook | `backend/app/core/auto_erp.py` - `rule_for`, `branch_matches`, `ready_script`, `fill_operator_defaults`, `maybe_auto_submit`, `safe_maybe_auto_submit` |
| Name / branch matching | `core/auto_erp_match.py` - `match_name`, `pick_branch`, `is_junk` |
| Column completion | `core/auto_erp_complete.py` - `complete_for_erp`, `fill_job_values`, `nearest_option` |
| Called from | `api/v1/jobs.py` - end of extraction and duplicate approval (`safe_maybe_auto_submit`); `_build_job_excel` (completion + `pre_check`); `_note_auto_fills` |
| Settings API | `api/v1/auto_erp.py` - `get_settings`, `set_allowed` (Super Admin only), `set_template_rule` |
| Screens | `frontend/src/components/AutoErpSettingsPanel.tsx` (in Super Admin → Tenants and Tenant Admin → Masters) |
| Job fields | `jobs.auto_erp_submitted`, `jobs.auto_erp_note`, `jobs.gk2_skipped` |

## Settings
- Tenant: `auto_erp_allowed` (Super Admin switch), `auto_erp_rules` (per template: `enabled`, `branches`)
- ERP script: `branch_field`, `status`
- Template: `entry_mode` (must be Excel), `use_customs_tariff`

## Design decisions
- **Two switches**: the SaaS Admin allows it per tenant; the Tenant Admin (or SaaS Admin) turns it
  on per template and branch. The customer decides, Axiora keeps a master switch.
- **Same submission path as manual jobs** - no second copy of the ERP flow that could drift.
- **Fill and note, don't stop** - the customer prefers fixing in the ERP over jobs waiting in Cargora.
