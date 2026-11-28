---
title: Operator guide (Gate Keeper 1)
category: user-guides
module: job-screen
owner: Senthil
status: ready
summary: Day-to-day guide for operators - find your jobs, check documents and values, fix product lines, submit to the ERP and recover failed jobs.
---
# Operator guide (Gate Keeper 1)

## Who this is for
You are a **Gate Keeper 1 (Operator)**. Shipment documents arrive by email (or you upload them),
Cargora reads them, and you check every value before the job goes to the ERP. This page walks
through a normal working day. Some field and section names on the job screen are set per
customer template, so the words on your screen may differ slightly from the examples here.

## 1. How to find my jobs
1. Log in. You land on your **Dashboard** ("Process transactions: upload, verify, and submit ERP
   entries"). Press **Go to Jobs** or use the left menu.
2. The **Workspace** menu on the left has five lists, each with a live count:
   - **New Jobs** - everything not yet sent to the ERP.
   - **Missing Documents** - a mandatory document has not arrived yet.
   - **ERP Failed Jobs** - the ERP entry did not go through.
   - **ERP Submitted Jobs** - the entry reached the ERP.
   - **Duplicate Emails** - pre-alerts held back because their BL / HAWB already came in.
3. Narrow the list with the filters above it: **Quick filter** (Today, Yesterday, Last 7 days,
   Last 30 days, This month, Previous month, All time), **From / To**, **Customer**,
   **ERP Branch**, **Assigned To** and **Type of shipment**. Your filters are remembered per list.
4. If you were given shipment modes, tabs ("All" plus one per mode, e.g. Air Import) appear at the
   top of the list.
5. Search by **Job No** (or **BL / HAWB** for import jobs) in the search box.
6. Press **✉ Check Email** to pull new document emails for you right now instead of waiting for
   the next automatic check.

## 2. How to open a job
- Click the job's row. It opens in a **new browser tab**, so your list stays where it was.
- The left rail now shows the job's stages instead of the main menu:
  **Document Capture → Required Details Review → Cross Docs Verification → IRN Processing →
  ERP Submission**. Use **← All jobs** at the top to go back.
- A stage only lets you move forward once it is finished; you can always *look* at any stage.
- **Print pages** in the header prints document pages from any stage.

## 3. How to add documents (Smart Upload)
On **Document Capture**:
1. Press **Smart upload (auto-classify)** and pick several files or a ZIP. Cargora sorts each page
   into the right document slot (Invoice, Packing List, Air Waybill ...). A combined PDF fills every
   type it contains; several invoices in one PDF each become their own row.
2. Or drop / upload one file per slot. **Remove** takes a wrong file out again.
3. Press **Run extraction** (or **Re-run extraction** later). **Extract this one** re-reads a
   single document.
4. To start a job by hand, use **+ New Job** on the Jobs list, pick the **Template set
   (customer)** and press **Create & open**. The Job No is generated automatically.

If the job shows **"Waiting for the zip password"**, type the zip password in the box and press
**Unlock and read**. Ask your Tenant Admin to save that password under Zip Passwords so the next
zip opens by itself.

## 4. How to check documents and fields
On **Required Details Review**:
1. Pick a document from the checklist in the left rail. The page image (**Document Preview**) is
   on the left, the **Extracted Data** on the right.
2. Read each value against the page. The colour key tells you where a value came from:
   *read off a document*, *from the reference sheet*, *worked out from a document*,
   *you changed it*, and *blank - nothing found* (red).
3. A red outline with "Mandatory - needs a value" must be filled before the document can be
   approved.
4. Press **Complete <document> Review** when a document is checked. **Undo** reopens it.
5. The last row in the checklist, **Additional Details**, holds questions the system asks a person
   (values not on any document). Answer all of them.
6. Wrong file? **Reupload** replaces it; **Delete Document** removes the file and its values.
   Re-run extraction afterwards.

## 5. How to fix a value
- Click into the box, correct it and save. Corrected values turn the "you changed it" colour.
- Fields with a fixed list (for example a quantity unit) are picked, not typed.
- A **CTH** is always 8 digits. Typing 4-7 digits offers matching codes from the Indian Customs
  Tariff when the template uses it.

## 6. How to work with product lines
- Each line item appears as its own card (**Product 1**, **Product 2** ...).
- A CTH typed on one line is carried to every other line that has not been given its own code.
  Tick **only this line** when this one product is different.
- **Delete** on a card removes a wrong line (only offered when there is more than one line).
- Values looked up from the customer's reference sheet sit on the line's own card; job-wide
  lookups are under **Dump Data**.

## 7. How to finish Cross Docs Verification and IRN
1. **Cross Docs Verification** compares the same field across documents (for example invoice
   total against packing list). Use **Accept** on a flagged row once you are satisfied.
2. Press **Approved and Proceed**. Open flags do not block you - pressing it accepts the data.
3. On **IRN Processing**, upload any supporting documents (name + files, **Upload**), then choose
   **Approval for IRN** or **Skip - nothing to attach**. Nothing here is mandatory.

## 8. How to submit to the ERP
On **ERP Submission**:
- If your company needs a second check for this shipment type, the button reads
  **Submit for Approval** - the job goes to Gate Keeper 2.
- Otherwise it reads **Submit to ERP** and the ERP entry starts straight away.
- For Excel-entry templates you also see **Edit ERP Excel** and **Download ERP Excel**. A saved
  edit shows "Edited Excel in use"; **Reset to generated** throws it away.
- If any field still needs an answer, a note lists them with **Fill them on Required Details
  Review →**.
- After submitting, a "Submitting to the ERP" popup moves you back to your list after a few
  seconds (**Go to dashboard now** skips the wait). The entry runs in the background.

## 9. How to handle a failed job ("Fix now")
1. When one of *your* jobs fails, a red **Fix now · Failed jobs (n)** button appears at the top of
   every screen. Press it to see only your failed jobs.
2. Open the job. On ERP Submission a fix card explains, in plain words, what the ERP refused:
   **You sent** / **ERP needs**, and **How to fix**.
3. Press **Fix the detail** (or **Edit ERP Excel** for an Excel column), correct the value outlined
   in red and save.
4. Come back and press **Retry** (or **Retry & Proceed**). Documents are not re-read - only the
   entry runs again.
5. "Not sent to ERP - ERP would refuse these values" means Cargora caught the problem before
   sending; nothing was saved in the ERP.
6. Still stuck? **Copy details for Cargora support** and send that text to support.

## 10. Notifications
The bell at the top right collects messages such as **Submitted to ERP**, **Failed to submit to
ERP**, **Waiting for a zip password** and **ERP job number changed**. Click one to open the job;
use **Mark all as read** or **Dismiss** to clear them.

## 11. Duplicates
- **"This job may already exist"** pops up when a job's data matches another job. **Open ... to
  compare**, then **Approve - it's a different job** (a genuinely separate shipment), **Delete** (a real repeat) or
  **Hold - decide later**.
- On **Duplicate Emails**, **Process as New Job** turns a held email into a job; delete removes it
  without creating a job.

## Common questions
**Why can't I move to the next stage?** A message under the stage says what is missing - usually a
document not yet reviewed or a mandatory field still blank.

**I can't see a job my colleague mentioned.** You only see jobs assigned to you or shared, for the
templates your admin gave you. Check the **Assigned To** and date filters too.

**Does Retry re-read the documents?** No. It re-runs only the ERP entry with your corrected values.

**Some jobs went to the ERP without me reviewing them.** Your company has Auto ERP Submission on
for that template; those jobs carry an "Auto ERP Submission" note listing any best-guess values.

**How do I change my password?** Click your name at the bottom of the left menu, then
**Change Password**.
