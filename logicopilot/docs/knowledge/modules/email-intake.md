---
title: Email intake & Smart Upload
category: modules
module: email-intake
owner: Senthil
status: ready
summary: How documents get into Cargora - the Email Puller, Smart Upload, zips (also locked ones), Excel, Word, images, pre-alert splitting, duplicates and large zips.
---
# Email intake & Smart Upload

## What it does
Every job starts with documents arriving. There are two ways in:

- **Email Puller** - Cargora reads connected mailboxes (Gmail or Zoho) on a timer. Each new
  pre-alert mail becomes a **job** of the template its mailbox is routed to.
- **Smart Upload** - an operator drops files onto a job. Cargora reads them the same way and
  puts each document into the right slot.

Both paths accept PDFs, images, zips (including zips inside zips and password-protected zips),
Word and Excel files. After intake the documents go to [classification](classification.md) and
then [extraction](extraction.md).

## How it works - Email Puller
1. **Poll.** A background thread runs `pull_inbox` every few minutes (setting
   `EMAIL_POLL_MINUTES`). Only one worker process runs the poller. If the previous cycle is still
   running, the new one is skipped. A Super Admin can pause all pulling (`email_pull_paused`),
   and each mailbox has its own pause switch on the **Email Puller** screen.
2. **Harvest.** For every connected, un-paused mailbox that belongs to an active tenant,
   Cargora connects over IMAP and fetches **today's unread** mail with PEEK, so nothing gets
   marked as read. This step is quick and runs one thread per mailbox.
3. **Claim.** Each mail is "claimed" by its Message-ID in the `email_seen` table before any work
   starts. That way two runs never process the same mail, and a mail that was already examined
   is never paid for again.
4. **Split pre-alerts (optional).** If the template has *pre-alert shipments* switched on, a mail
   that covers several HAWBs is split into one task per shipment (see below).
5. **Duplicate check.** The BL / AWB number is read from the **subject** (free, no OCR). If the
   same sender already sent the same number in the last 30 days and it became a job, the mail is
   held on the **Duplicate Emails** list instead of becoming a second job.
6. **Unpack attachments.** Zips are opened at any depth (see rules below). Images that are not
   PDF/PNG/JPG (TIFF, BMP, WebP …) are turned into PDFs. Word becomes one PDF. Excel becomes
   one PDF per sheet, or is read sheet by sheet from its cells on an *Excel by sheet* template.
7. **Create the job early.** A mailbox routed to one template creates the job and its empty
   document slots straight away, so the operator sees it while OCR is still running.
8. **OCR + classify.** Every page is read and matched to a template slot. Pages that cannot be
   placed are kept as **Unclassified** - they are never thrown away.
9. **Extract.** If the template has *Start extraction automatically* on, extraction starts once
   every required slot is filled.
10. **Record.** The outcome (matched, split, duplicate, held, no match …) is written to
    `email_seen`. The mail is deliberately left **unread** in the inbox; `email_seen` is what
    stops it from being processed again.

A shared inbox that is not routed to a single template has to work out **which customer** the
documents belong to. The AI reads the documents and matches them against each template's
identifying values (consignee, IE code, GSTIN …). If nothing matches, or more than one
template matches, the mail goes to **Pending Mail** so a Super Admin can choose the template
by hand.

## How it works - Smart Upload
1. The operator drops one or more files on a job (`smart_upload`). Files are sent **together in
   one request**, so the classifier can weigh them against each other (for example, three
   invoices land as three rows in the Invoice slot).
2. The same unpacker, image conversion, Word/Excel handling and classifier as email are used
   (`_classify_and_place_files`).
3. A locked zip asks for its password in the upload dialog. There are also options to save the
   password for this customer, or to read the files without the locked zip.

## Special cases
**Password-protected zips.** Saved passwords for the tenant are tried first - those scoped to
this template or sender domain come first, then the most recently used. If none works, the job
is created as **Waiting for Password**: slots empty, nothing read, nothing billed. The operator
enters the password on the job (or chooses *Process without locked files*). Then the whole mail
is read **once**, into that same job. Five wrong passwords lock entry for 15 minutes. Tenant
Admins manage passwords on the **Zip passwords** screen. Passwords are stored encrypted and are
never shown again.

**Pre-alert split.** One air pre-alert can list several HAWBs (in the subject after `//`, or in
a table in the body). The AI reads one record per shipment. Each attachment is matched to its
shipment by the number in its file name, then by its text, then by asking the model. Each
shipment then runs as if it were a separate mail. If a shipment has no Air Waybill attached, a
one-page **Air Waybill (Pre-Alert)** PDF is built from the mail's own data.

**Air consolidation zips.** When a zip has a main folder and sub-folders, the Air Waybill is
taken only from the main folder. Waybills found in sub-folders go to Unclassified.

**Large zips.** On the Document Processing page, a template can:
- *Hold extraction for a large zip* - the documents are read and placed, then the job waits for
  **Run extraction** so someone can check them first;
- *Use the large-job lane* - a mail whose biggest zip is over the size limit goes to its own
  worker, one large job at a time, so it does not hold up the mail cycle for everyone else.

**AI service down.** If an AI call fails (quota, rate limit, outage), the mail is not marked as
"no match". It is held for a 15-minute wait and tried again later. Super Admins see these mails
on the held-emails card and can rerun them.

## Business rules
| Rule | Why |
|---|---|
| A mail is claimed by Message-ID before any OCR | OCR and AI cost money; a mail must never be paid for twice or made into two jobs |
| Duplicate BL check runs before anything is downloaded | A re-sent pre-alert would otherwise cost a full OCR + AI run and give the operator two identical jobs |
| Duplicate check is per sender, 30-day window | Two customers can legitimately quote the same carrier number |
| Nothing a duplicate mail contains is thrown away | A person decides: **Delete** or **Process as new job** |
| Zip limits: depth 5, 300 files, 300 MB total, 50 MB per file, suspicious compression ratio refused | Protection against zip bombs; limits apply only to what comes out of a zip |
| Files that could not be read are listed on the job ("Files not read") | Nothing should vanish without a trace |
| Signature images, calendar invites, `.DS_Store` etc. are skipped silently | They are never documents and would only clutter the list |
| A locked zip holds the whole job | Big consolidations take a long time to read, so they are read once, complete, not half now and half later |
| Excel by sheet: an Excel sheet with the same invoice number as a PDF already placed goes to Unclassified | The PDF is kept as the source |
| Excel sheets are billed at intake: 20 data rows = 1 page, minimum 1 per non-empty sheet | Classification and server work cost money even when a sheet is not extracted |
| Pre-alert split only when at least two shipments are found, each with its own attachment | A mail that only *looks* like several numbers must never become empty jobs |
| A HAWB the model reports but that is not printed in the mail is ignored | Prevents invented shipments |
| A switched-off tenant's mailboxes are never read | Tenants > Disable must fully stop intake |

## Where in the code
| What | Where |
|---|---|
| Poller, harvest, claim, per-message pipeline | `backend/app/core/email_puller.py` - `start_scheduler`, `pull_inbox`, `_pull_inbox`, `_harvest_mailbox`, `_claim`, `_process_one_message` |
| Pre-alert split | `core/prealert_shipments.py` - `plan_split`, `read_shipments`, `assign_attachments`, `build_awb_pdf`; puller side `_split_into_shipment_tasks`, `_settle_split_parent` |
| Zip unpacking (shared) | `core/attachment_unpack.py` - `unpack_attachments` |
| Zip passwords | `core/zip_passwords.py` (`candidate_passwords`, `save_password`); `email_puller._hold_for_password`, `resume_held_job`; endpoint `jobs.unlock_held_job`; API `api/v1/zip_passwords.py` |
| Excel by sheet | `core/excel_intake.py` - `intake_excel_files`; cell reading `core/excel_text.py` |
| Word / Excel to PDF | `core/office_convert.py` (LibreOffice) |
| Other images to PDF | `core/image_convert.py` - `convert_files` |
| Duplicate BL | `core/duplicate_bl.py`, `core/email_bl.py`; screen API `api/v1/duplicate_emails.py` |
| Unmatched mail | `api/v1/pending_mail.py` (Super Admin) |
| Large-job lane | `core/large_job_lane.py`; `email_puller._hand_large_mail_to_lane`, `_large_mail` |
| Optional Redis mail queue | `core/mail_queue.py` |
| Smart Upload | `api/v1/jobs.py` - `smart_upload`, `_classify_and_place_files` |
| Mailbox management | `api/v1/email_puller_mailboxes.py`; screen `frontend/src/pages/super-admin/EmailPullerPage.tsx` |
| Screens | `pages/jobs/DuplicateEmailsPage.tsx`, `pages/jobs/HeldPasswordPanel.tsx`, `pages/super-admin/PendingMailPage.tsx`, `pages/super-admin/HeldEmailsCard.tsx`, `pages/tenant-admin/ZipPasswordsPage.tsx` |

## Settings
- Server (`.env`): `EMAIL_POLL_MINUTES`, `MAIL_QUEUE`, `GMAIL_USER` / `IMAP_HOST` (old shared inbox only), `PARALLEL_OCR_WORKERS`.
- Platform (system settings): `email_pull_paused`, `email_poll_workers`, `large_job_parallel_calls`.
- Per template (Document Processing / Template Settings): `prealert_shipments`, `auto_extract`,
  `large_zip_hold`, `large_zip_mb`, `large_job_lane`, `excel_mode`, `excel_ignore_patterns`.
- Per mailbox (Email Puller): tenant, template, paused.

## Design decisions
- **Harvest then process.** Fetching is cheap and runs one thread per mailbox. The expensive
  OCR/AI work goes through one shared pool, so a quiet mailbox's worker can help a busy one.
- **Mailbox routed to one template = trusted.** No customer matching is needed, and the job
  appears immediately. Shared inboxes still match by document content, never by sender address.
- **Password zips: hold the whole job** was chosen over "process what's readable now, add the
  rest later". The latter is only available through *Process without locked files*.
- **Large jobs = zip size only** (default over 5 MB), handled one at a time, so small jobs keep
  their normal speed.
- **Pre-alert split happens before any job exists**, in the task list, so the shipments run side
  by side instead of one after another.
