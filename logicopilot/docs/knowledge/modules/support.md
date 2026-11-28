---
title: Production Support (support desk app)
category: modules
module: support
owner: Senthil
status: ready
summary: Axiora's support desk screen - every customer's health, failed and delayed jobs, Retry, notes and alerts.
---
# Production Support

## What it does
Production Support is the screen for **Axiora's own support desk**. One place to see, across
**every customer**, whether Cargora is healthy, which jobs failed or are running late, and why -
so the desk can act before the customer phones.

The desk is **read-only**, with one exception: it may press **Retry** on a job that failed
(a failed ERP entry, or an extraction that never finished). It can also mark a problem as
handled and keep a log of notes and customer calls.

The same data is on the phone through the **Cargora Support app** (see the Support phone app page).

## Who uses it
- Role `support` (created by the Super Admin under **Users & Tenant Admins > Production Support Users**).
  After login they land on `/support`.
- The backend endpoints also accept `super_admin`, but the website pages are shown to the `support` role only.

## The pages
| Menu item | Route | What it shows |
|---|---|---|
| Dashboard (Overview) | `/support` | Tiles (submitted, auto / manual, in review, failed, delayed), health, pipeline, performance against SLA, jobs per day, items needing attention, failed / delayed list, recent activity |
| Application Health | `/support/health` | One green / amber / red box per part of the system, queues and servers, plus **Alerts & SLA** settings |
| Job Monitoring | `/support/jobs` | Every customer's jobs, filters (customer, stage, view, reference), time per stage (red when over SLA), who has the job |
| Failed / Delayed Jobs | `/support/issues` | Open problems with reason, age and Retry; tabs All / Extraction / ERP / Other; "show handled" |
| Processing Performance | `/support/performance` | Average and slowest (90th percentile) time per stage against the SLA, and the jobs that went over |
| Customer View | `/support/customers` | Per customer: jobs this period, submitted, failed, in progress, open problems, last job |
| Issues & Resolution | `/support/activity` | The support log: notes, customer calls, handled, retries, alerts - and a form to log a call or note |

Period pickers default to **Today** (also 7 days / this month). Times are shown in IST.
"Investigate" opens the normal job screen, read-only for Support.

## How it works

### 1. Where a job is (pipeline)
Each job is placed in one stage: Received, AI Processing, Ready for Review, In Review,
Waiting Approval, ERP Submission, ERP Submitted, Failed. The rules for "Failed" and
"ERP Submitted" are the same ones the main job list uses, so a tile and the job list always agree.

### 2. Finding open problems
Among jobs from the last **60 days** that are not completed, a job is an open problem when:
- **ERP failed** - the job shows the red Failed badge. The reason is the first item of the
  ERP-rejection card ("ERP did not accept X: needs Y"), else the ERP diagnosis, else the ERP reason.
- **ERP slow** - ERP entry still running past the ERP SLA.
- **Extraction slow** - still reading documents past the extraction SLA.
- **Extraction failed** - still a draft with files, more than **10 minutes** old.
- **Missing documents** - still a draft, waiting for required documents.

### 3. Retry
Retry uses the **existing job endpoints**, so the website, phone app and operators behave the same:
- **Retry ERP** = `POST /jobs/{id}/gk2/approve` - allowed for Support **only when the ERP run has failed**.
- **Retry extraction** = `POST /jobs/{id}/extract` - allowed for Support **only for a job still in draft**
  (an extraction that never finished). Support can never re-read an already extracted job.

Each retry is written to the job history and to the support log.

### 4. Mark handled
"Mark handled" (with an optional note) hides the problem until the job **fails again**. It also
closes the job's open ERP failure reports and adds a line to the job history.

### 5. Measuring time per stage (SLA)
| Stage | Measured from -> to | Default SLA |
|---|---|---|
| Email to AI start | job created -> first "extracting" event | 2 min |
| Queue waiting time | the run's "extracting" event -> reading actually began | 3 min |
| AI extraction | reading began -> finished | 4 min |
| ERP submission | ERP run started -> submitted or failed | 5 min |

Extraction and queue times are recorded only from 27 Nov 2026; older jobs show none.

### 6. Application Health
Boxes: Web Application, Servers, Database, Redis / Queues, Email Puller, AI / OCR Services,
ERP Automation, Storage (uploads), Database backup. They read what already exists:
process heartbeats (every process writes one), a quick database query, queue status, mailbox
pause flags, ERP branch login status, free disk space, and the nightly backup's status in Redis.
Every check is wrapped: if one check breaks, that box shows "unknown" - the page still loads.
Nothing on this page changes anything.

### 7. Alerts
Every **5 minutes** one process (a Redis lock picks it) checks for:
- a health box that is DOWN,
- a stuck queue,
- new failed jobs since the last check,
- jobs past the extraction or ERP SLA.

Each alert is written to the support log (kind "alert"), sent as a **push notification** to
registered support phones, and **emailed** to the saved alert addresses. The same alert is not
repeated within **60 minutes**. "Send test" on Application Health checks email setup.

## Business rules
- **Support sees all customers** - it is a cross-tenant role with no tenant of its own. Reason: one desk supports every customer.
- **Read-only except Retry.** Reason: the desk should fix stuck runs, not change customer data. Retry rules are enforced on the server, not just by hiding buttons.
- **Support reads only job pages** of the admin-readable endpoints (jobs, template lookups a job screen needs, customer names) - never the user list, settings, templates or billing.
- **ERP retry only on a failed run.** Reason: the first approval belongs to Gate Keeper 2.
- **Extraction retry only on a draft.** Reason: re-reading an extracted job would replace values the operator may already have corrected.
- **An unfinished extraction is "failed" only after 10 minutes**, so a job still arriving or queued is not flagged.
- **Alerts without email still show on screen.** If email is not set up on the server, alerts are logged and pushed; only the email is skipped.
- **Alert addresses**: at most 20, stored lower-case. SLA values are whole minutes, at least 1.

## Where in the code
| Part | Code |
|---|---|
| Desk API | `backend/app/api/v1/support.py` - `overview`, `issues`, `mark_handled`, `performance`, `health`, `jobs_list`, `customers`, `activity`, `add_activity`, `get_settings_`, `put_settings`, `test_alert`; helpers `_issues`, `_pipeline_stage`, `_timings`, `_breaches`, `_reason` |
| Health boxes | `backend/app/core/support_health.py` - `components` |
| Alerts | `backend/app/core/support_alerts.py` - `get_config`, `set_config`, `find_alerts`, `check_once`, `send_email`, `start_alert_loop` |
| Push to phones | `backend/app/core/support_app.py` - `send_push` |
| Retry | `backend/app/api/v1/jobs.py` - `gk2_approve`, `extract_job` (the `SUPPORT` checks) |
| Read access rule | `backend/app/core/deps.py` - `_SUPPORT_READ_PREFIXES`, `_support_may_read`, `require_role` |
| Support log table | `backend/app/models/support_activity.py` - `SupportActivity` (kinds: note, customer_call, handled, retry_erp, retry_extraction, alert) |
| Heartbeats / queues | `backend/app/core/queue_status.py` |
| Website pages | `frontend/src/pages/support/` - `SupportOverviewPage`, `SupportHealthPage`, `SupportJobsPage`, `SupportIssuesPage`, `SupportPerformancePage`, `SupportCustomersPage`, `SupportActivityPage`, `IssuesTable`, `supportUi` |
| Menu and routes | `frontend/src/components/AppShell.tsx`, `frontend/src/routes/routes.tsx` |

## Settings
- In the app (Application Health > Alerts & SLA, stored in system settings `support_config`): SLA minutes per stage, alert email addresses.
- Server `.env` for alert email: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`.
- Redis is needed for the alert lock (a file lock is used without it), heartbeats and the backup box.

## Design decisions
- **Same login page**; the `support` role simply lands on `/support`. No separate site.
- **Built from data the system already keeps** (job statuses, job events, queue heartbeats) - no change to the ERP worker was needed.
- **WhatsApp alerts were dropped** by request; email plus phone push instead.
- **SLA defaults** came from the agreed mock-up: email to AI 2, extraction 4, queue 3, ERP 5 minutes.
- **Retry reuses the job endpoints** instead of new ones, so every channel follows the same rules.
