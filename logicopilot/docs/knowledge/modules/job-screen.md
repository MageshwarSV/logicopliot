---
title: Jobs list and job screen (GK1 / GK2 review)
category: modules
module: job-screen
owner: Mageshwar
status: ready
summary: The Jobs list, the job screen where GK1 reviews and GK2 approves, assignment, job statuses, failed ERP jobs and the E-Docket.
---
# Jobs list and job screen (GK1 / GK2 review)

> This area (the job screen `JobRunPage.tsx`, the field editor, Template Settings) is owned by
> **Mageshwar**. Agree any change with him first. This page describes how it works today.

## What it does
Every pre-alert or upload becomes a **job**. The **Jobs list** shows the jobs a user is allowed
to see, grouped into screens (New, Missing Documents, ERP Failed, ERP Submitted). Opening a job
opens the **job screen**, where the operator (**Gate Keeper 1, GK1**) checks every extracted
value beside the document, corrects it, and submits. Where the customer wants a second check,
**Gate Keeper 2 (GK2)** gives the final approval before the job goes to the ERP. The
**E-Docket** page prints the customer's paper job docket filled from the job.

## How it works, step by step

### 1. The Jobs list
- Four sidebar screens, chosen by `?category=` in the link:

| Screen | Which jobs |
|---|---|
| New Jobs | Not yet sent to the ERP, not failed, not missing documents |
| Missing Documents | A mandatory document slot has no file |
| ERP Failed Jobs | The ERP run failed (`gk2_status` or `status` is failed) |
| ERP Submitted Jobs | Sent to the ERP at least once and not failed (or completed) |

- A plain `/jobs` visit shows every category. The old separate "ERP Verified Jobs" screen was
  removed on 2026-11-10; "completed" is now the same as "submitted".
- Filters: quick date range, customer, ERP branch, assignee, shipment type, from / to dates.
  They are remembered per screen in the browser, so opening a job (a new tab) and coming back
  keeps them.
- Rows load in small batches; the first batch is tiny so something shows at once, the rest
  streams in.

### 2. Who sees which job
- **Operator with assigned shipment modes** (Sea Import, Air Export ...): every job of those
  modes - the mode replaces ownership as the access gate.
- **Operator with no modes**: unassigned jobs plus jobs assigned to them.
- **GK2**: only jobs of their assigned modes, and - if the Tenant Admin set a list - only jobs
  of the GK1 operators they are allowed to see.
- **Manager**: every job in the company, read only.
- The same checks run when a job is opened directly by its id, not only on the list.

### 3. Assignment
- A mail-pulled job is created for the template's mail operator; a job an operator creates is
  assigned to them.
- On the Jobs list, an operator, admin or Super Admin can set or clear the assigned operator
  from a dropdown. Reassigning simply overwrites; there is no locking. Only an operator of the
  same company can be assigned.

### 4. The job screen (stages)
The job screen walks through these tabs:

| Tab (shown as) | What happens |
|---|---|
| Document Capture | Files are classified into the template's document slots |
| Required Details Review | The extracted values, document by document, plus "Additional details" (questions for the operator) |
| Cross Docs Verification | Cross-document mismatches (e.g. invoice total vs packing list) to accept or fix |
| IRN Processing | Only where the job needs IRN documents |
| ERP Submission | Final submit; the live ERP check; Download / edit ERP Excel |

The backend also tracks "Dump Data" and "Manual Data Entry" as operator stages.

- A job cannot leave **Cross Docs Verification** while a blocking mismatch is not accepted -
  even a template with no cross-document checks still needs a human to look at the data once.
- While reviewing, the **live ERP check** (`LiveErpCheck`) shows the values the ERP would refuse,
  re-checked after every save, on Required Details Review and ERP Submission.
- Field labels come only from the template's `display_label` (since 2026-11-28).

### 5. GK1 submit and GK2 approval
- GK1 presses **Final Submit for Approval**. It requires Cross Docs Verification approved.
- If GK2 is required for the job's shipment mode, the job goes to **Pending GK2 Approval**.
- If the Tenant Admin turned GK2 off for that mode, the button reads **Final Submit** and the
  job goes straight to the real ERP run (`gk2_skipped`).
- GK2 opens the job, can do its own validation pass, and presses **Final Approve & Proceed**,
  or sends it back for GK1 to fix (reopen).
- On approval: "AI - Preparing for ERP" (the ERP Excel is built and checked), then "ERP Entry
  Process Started" (the ERP robot runs), then "AI - ERP Submitted" or "Failed".

### 6. Statuses shown on the list and header
Pending GK2 Approval · IRN Document Process · AI - Preparing for ERP · ERP Entry Process
Started · AI - ERP Submitted · Failed · AI - Processing / AI - Re-Extraction · Missing Documents
· Document Capture · GK1 Review (nothing touched yet) · GK1 Reviewing (operator has started) ·
Ready for Submission · Submitted · Duplicate / Possible Duplicate.

### 7. Failed jobs
- A failed ERP run leaves the job's data as extracted so it can be retried; the job opens on
  ERP Submission with the failure banner.
- If the ERP refused values, the **Fix card** (`ErpFixCard`) lists one row per Cargora box: what
  was sent, what the ERP wants, a Fix button that opens the box outlined in red, and a tick once
  it is changed. A column no box feeds says it needs Cargora support.
- After fixing, **Retry & Proceed** runs the approval again. GK1 can retry a failed run; GK2
  can retry from its own screen. No admin step is needed in between.

### 8. E-Docket
- `/jobs/{id}/e-docket` reproduces the customer's paper docket (front and back page), filled
  from the job's values, the Bill of Lading and the job's ETA.
- The operator's choices and edits (transaction mode, priority, FCL / LCL, edited rows, back
  page tables) save as they edit, so the docket prints the same from the E-Docket page and from
  Print pages.

## Business rules
- **One access rule for list and open** - hiding a job from the list alone did not stop opening
  it by id, so the same gate runs in both places.
- **GK2 is required by default** for any mode with no setting, so existing customers keep the
  same flow.
- **A failed job is never a dead end** - Retry & Proceed after a fix, without support.
- **Status words sit on top of the stage, never feed back into it** - the badge reads better
  without changing what gates a job's progress.
- **No "Missing Documents" while a mail job is still being classified** - the puller marks the
  job as "Document Classifying" until its attachments are placed.
- **Auto ERP jobs** skip review entirely (see the Auto ERP page).

## Where in the code
| Area | Code |
|---|---|
| Jobs list | `frontend/src/pages/jobs/JobsPage.tsx` |
| Job screen | `frontend/src/pages/jobs/JobRunPage.tsx` (`STEPS`, `gk2ApproveAndProceed`) |
| Fix card / live check | `frontend/src/pages/jobs/ErpFixCard.tsx`, `LiveErpCheck.tsx` |
| ERP Excel editor | `frontend/src/pages/jobs/ErpExcelEditor.tsx` |
| E-Docket | `frontend/src/pages/jobs/EDocketPage.tsx`, `backend/app/api/v1/edocket.py` |
| Categories and status words | `backend/app/api/v1/jobs.py` - `_category_condition`, `_job_category`, `_job_stage`, `_outer_status`, `_OPERATOR_STAGES` |
| Access | `backend/app/api/v1/jobs.py` - `_load_job`, `list_jobs` |
| Assignment | `backend/app/api/v1/jobs.py` - `assign_job_operator` |
| GK1 / GK2 | `backend/app/api/v1/jobs.py` - `submit_for_gk2_approval`, `reopen_gk2_submission`, `gk2_approve`, `_gk2_required_for_job`, `_finalize_job_approval` |
| Job table | `backend/app/models/job.py` - `Job` |

## Settings
- Tenant: `gk2_required_by_mode`.
- User: `assigned_modes`, `assigned_operator_ids`.
- Template: `pull_operator_id`, `mode`.
- Job: `status`, `gk2_status`, `gk2_skipped`, `assigned_operator_id`, `stage_override`,
  `validation_approved`, `gk2_validation_approved`, `edocket_state`.

## Design decisions
- **GK2 is a chain on top of the job status**, not more status values: `status` says what
  happened to the data, `gk2_status` says where the approval chain is.
- **Labels from the template only** (2026-11-28): the hard-coded label lists were moved into each
  template's `display_label` and removed from the code. About 95 internal field names that
  behaviour depends on were kept by design.
- **On hold:** moving the job screen's remaining hard-coded screen rules (section order, widths,
  dropdown options, relocated fields) into template settings. Designed, waiting for Mageshwar;
  no change until it is picked up again.
