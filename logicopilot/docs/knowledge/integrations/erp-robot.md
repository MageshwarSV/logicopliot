---
title: ERP robot, scripts and the ERP queue
category: integrations
module: erp-robot
owner: Mageshwar
status: ready
summary: The Playwright browser robot that types jobs into the customer's ERP - recorded scripts, the durable queue, shared and branch logins, the Entry Browser and "Invalid Data Found".
---
# ERP robot, scripts and the ERP queue

> **Read-only for everyone except Mageshwar.** `backend/app/core/browser.py` and the ERP script
> code are his. Other work (rules, Auto ERP, failure analysis) must stop problems *before* the
> robot runs, never by editing the robot.

## What it does
Once a job is approved (or sent by Auto ERP), Cargora has to put it into the customer's ERP
(today LiveImpex / SOFTLINK). There is no API for that, so a **robot** does what an operator would:
it opens a real Chromium browser (Playwright), logs in, moves through the ERP screens, fills the
header values, uploads the ERP import Excel and presses Save. It then reads back the ERP's job
number (e.g. `BLR/AI/4070/26-27`) and writes the result on the job.

What the robot clicks is not hard-coded. A Super Admin **records** an ERP script once per
customer/stream in the ERP Script Recorder, and the robot replays it for every job.

## How it works
1. **Recording a script** (Super Admin → ERP Scripts → Recorder). The recorder runs a live
   browser on the server and streams screenshots. Clicks and typing are relayed by position; the
   element under the cursor becomes a replayable selector (with its iframe chain, if any). Each
   action is saved as a **step**. Step values can be fixed text or a job field placeholder
   `[[Field label]]`.
2. **Checkpoint and "Stay open".** One step can be marked the **checkpoint**: the point after
   login and navigation where the entry screen is open. Steps before it are setup; steps after it
   are the entry itself. With a checkpoint and "Stay open", the login is saved and reused.
3. **A job is queued.** `_kick_off_real_erp_submission` (in `api/v1/jobs.py`) sets the job to
   "Preparing for ERP" and puts its **job id** (not a function) on the ERP queue.
4. **A worker takes it.** The worker runs `_run_gk2_submission`: builds the workbook, finds the
   tenant's `ready` ERP script for the template, attaches the Excel to the upload step and calls
   `play_steps`.
5. **Replay.** `play_steps` / `_play_steps` go step by step. Before **every** step it checks for
   the ERP's "Invalid Data Found" popup. A step that never moves on is aborted after the stall limit.
6. **Result.** `persist_run_outcome` (in `jobs.py`) is the single place that writes what a run
   did: status, captured values (ERP job number), the screenshot of where a failed run stopped,
   and any ERP alerts the script did not cover.

## Step types
Recorded steps include: navigate, click, double click, fill, select, autocomplete, radio, check,
pick date, upload, download, get text, assert text, screenshot, wait / wait for / wait until gone /
wait for full load, scroll, hover, send keys, switch tab, row action (click a control in the table
row that holds some text), dialog (how to answer a native alert), **If / Else**, **AI action** and
submit.

- **If / Else** (`_if_condition_met`) tests either "is this text on screen" (a job field or fixed
  text) or "is this element on screen", polling for a few seconds because popups are often still
  drawing. If the step has an iframe chain, the test looks **only inside that frame** - for example
  "is Chennai in the branch popup", not "is Chennai anywhere on the page", because the header also
  shows the current branch.
- **Iframes.** Each step stores the chain of frames it was recorded in; the robot searches inside
  that chain, and tries other frames when the element has moved (`_elsewhere`, `_frame_paths`).
- **Native alerts.** Dialog steps become rules for answering ERP pop-ups. An alert the script did
  not cover is pressed through and then written back into the script as an "if this alert comes"
  step so the Super Admin can choose its button from then on.
- **AI takeover.** When a recorded element is missing, the AI may read the screen and act. On
  normal jobs, the Entry Browser and test plays it only does this on steps the Super Admin marked
  with the brain icon (`ai_zone`). On Auto ERP jobs it may act anywhere, because nobody is watching
  them. An explicit AI action step always runs.

## Dropdown matching
ERP dropdowns rarely hold exactly the extracted text. `_strong_pick` tries the sure matches first:
1. exact text (spaces, case and punctuation ignored);
2. the option's **name** is the value (`3` → `3 (3)`, `ORAGADAM` → `ORAGADAM (3)`), then an
   option that starts with the value as a whole word;
3. the bracketed **code** is the value.

Only then does `_fuzzy_pick` try looser matches: substring, then the same words, then the closest
spelling (similarity 0.6 or more). **Why:** on the quotation Address list, `3` once matched two
options and the shorter one won by length. A value must win by *being* the option, not by luck.

## The ERP queue (FIFO, survives restarts)
- `core/erp_redis_queue.py` keeps the queue in Redis as data (`{job_id, is_excel_entry}`).
  New jobs go on one end and workers take from the other, so the **oldest job runs first**.
- A worker moves the item to a "processing" list while it runs. If the process is killed,
  `requeue_orphans()` puts those items back at the **front** of the queue at startup.
- This is "at least once": safe because `_run_gk2_submission` does nothing if the job is no
  longer "Preparing for ERP".
- Redis is optional. With no Redis (or Redis down) the app falls back to the in-memory pool in
  `core/erp_queue.py` (`submit_erp_run`). A queue problem must never stop a job.
- `core/restart_resume.py` (`resume_interrupted_work`) also re-queues, oldest first, every job
  left on "Preparing for ERP" or "Entering ERP" after a restart. A run that was inside the ERP
  starts again **from step 1** (agreed 2026-11-20), because the half-filled ERP form is gone.

## Logins
SOFTLINK allows one login per ERP user: a second login offers to kill the first, which would log
another job out mid-entry. Many **tabs** on one login are fine. So:

- **Shared login** (`core/erp_session.py`). For a script with a checkpoint and Stay open, the
  first job to find no saved session logs in; the others wait for it (file lock) and then load the
  saved session and go straight to the entry screen. A script *without* a checkpoint cannot share,
  so its jobs take turns (`script_slot`). Every path ends in "log in yourself" if waiting fails -
  a redundant login is a smaller problem than a job that never runs.
- **Parked sessions** (`core/parked.py`). A Stay-open script can keep a browser parked at the
  checkpoint, waiting for the next job; it is rebuilt every 20 minutes and after each job.
- **Branch logins** (`core/branch_sessions.py`, table `erp_branch_sessions`). In LiveImpex the
  branch belongs to the login, not the tab - switch one tab to Bangalore and every tab on that
  login is in Bangalore. So each branch gets its **own ERP user**, kept logged in by a keeper
  thread (health check every 5 minutes, re-login with back-off). A script with a **branch field**
  routes each job to the branch login whose name matches the job's value (`branch_for_job`), and
  up to "max tabs" jobs per branch run at once (`play_on_branch`). Jobs never log in themselves.
  If there is no matching branch login, the job **fails with a reason** instead of falling back
  to the script's own login, which could log the branch user out under every other job.
- Users can be allowed to turn a branch login on/off from the top bar (`/erp-branch-sessions/mine`,
  `/switch`).

## Entry Browser
Super Admin → ERP Entry Browser (`ErpEntryBrowserPage.tsx`, `api/v1/entry_browser.py`) lists failed
jobs per company and template, and can **re-run a job live** (watch the screenshots) or **step by
step** (click, type, select by hand). Its result goes through `persist_run_outcome`, the same as a
queued run. While a manual run is active it takes a **hold** (`core/erp_priority.py`): a queued run
already in the ERP stops at the end of its current step, goes back to "Preparing for ERP" and later
starts again from step 1; queued runs not yet started wait. A hold ends when the manual run or its
browser ends (2-hour ceiling).

## "Invalid Data Found" and other failures
- SOFTLINK answers a bad Excel import with an **"Invalid Data Found"** table (Sheet / Field /
  Description). The robot stops **as Failed** before the next step, with the ERP's words and a
  screenshot (`_import_rejection`, `_rejected_import_block`). It never clicks past it - after the
  LiveImpex update of 2026-11-27 the popup no longer blocks the screen, and steps used to carry on
  and save an entry the ERP had refused.
- `core/erp_rejection.py` (`build_help`, `parse_rows`, `build_items`) turns the table into a short
  fix list: each ERP column is traced (by code, not AI) to the Cargora box that fed it, duplicates
  are merged, and one AI call words each item in plain English. Without the AI the ERP's own rule
  is shown. A column no box feeds is marked as needing Cargora support.
- A form that suddenly locks almost all fields is treated as blocked/duplicate (`_detect_block`).
- A failed job shows "Failed" with the reason; the operator fixes and presses **Retry**.

## Where in the code
| What | Where |
|---|---|
| Recorder + replay engine | `backend/app/core/browser.py` - `RecorderSession`, `play_steps`, `_play_steps`, `_perform_step`, `_strong_pick`, `_fuzzy_pick`, `_if_condition_met`, `_import_rejection` |
| Queue | `core/erp_redis_queue.py` (`enqueue`, `requeue_orphans`), `core/erp_queue.py` (`submit_erp_run`) |
| Restart recovery | `core/restart_resume.py`, `core/parked.py` (`resume_all`) |
| Logins | `core/erp_session.py`, `core/branch_sessions.py` |
| Manual-run priority | `core/erp_priority.py` |
| Job side | `api/v1/jobs.py` - `_kick_off_real_erp_submission`, `_run_gk2_submission`, `persist_run_outcome` |
| ERP refusal help | `core/erp_rejection.py` |
| APIs | `api/v1/erp_scripts.py`, `api/v1/entry_browser.py`, `api/v1/erp_branch_sessions.py` |
| Screens | `frontend/src/pages/super-admin/ErpScriptsPage.tsx`, `ErpScriptRecorderPage.tsx`, `ErpEntryBrowserPage.tsx`, `BranchLoginsCard.tsx` |
| Tables | `erp_scripts` (`models/erp_script.py`), `erp_branch_sessions` |

## Settings
- System settings: `erp_script_workers`, `erp_script_workers_auto`
- Server config: `browser_headless`, `max_parked_sessions`, `redis_url`, `erp_sessions_dir`
- Per script: `status` (draft / ready), `checkpoint_index`, `stay_open`, `branch_field`, `template_ids`
- Per branch login: `branch`, `max_tabs`, `enabled`, `script_ids`

## Design decisions
- **Record, don't code.** Each ERP is a recorded script so a new customer needs no robot code.
- **Queue holds data, not functions**, so a deploy no longer loses waiting jobs.
- **One login per ERP user, many tabs.** Logins are single-flighted; branches get separate users.
- **Interrupted runs restart from step 1**, never from the middle.
- **Stop at "Invalid Data Found"** rather than risk saving a refused entry.
- **AI only where marked** on watched jobs (4S, 2026-11-26: the AI had become too eager).
