---
title: Reviewer guide (Gate Keeper 2 and Manager)
category: user-guides
module: job-screen
owner: Senthil
status: ready
summary: What Gate Keeper 2 reviews and approves before the ERP entry, and what a Manager can see - including which shipment modes each one sees.
---
# Reviewer guide (Gate Keeper 2 and Manager)

## Who this is for
- **Gate Keeper 2** - gives the final approval before a job goes to the ERP.
- **Manager** - a read-only view of every job in the organization.

Gate Keeper 1 (the operator) does the first full review - see the *Operator guide*. Gate Keeper 2
is a second check, used only where your company wants one. Field and section names on the job
screen come from each customer template, so your screen may word them slightly differently.

## Part A - Gate Keeper 2

### 1. Which jobs you see
- A job reaches you only after Gate Keeper 1 presses **Submit for Approval**. Jobs still with an
  operator do not appear.
- You see jobs of the **shipment modes** (Air Import, Sea Export ...) your Tenant Admin gave you.
  The modes come from the templates ticked on your user account ("Templates this user reviews").
  No modes means an empty list - ask your Tenant Admin.
- Your Tenant Admin can also limit you to certain operators ("Operators whose jobs this user
  reviews"). By default you see every operator's jobs.
- Jobs you already approved stay in your list, so you can follow what happened to them.
- Whether a shipment type needs Gate Keeper 2 at all is set by the Tenant Admin on **Masters**
  ("Gate Keeper 2 review required" or "Goes straight to ERP after Gate Keeper 1"). If it is off,
  those jobs never come to you.

### 2. How to find jobs waiting for you
1. After login you go straight to the jobs list. The **Workspace** menu shows **New Jobs**,
   **Missing Documents**, **ERP Failed Jobs**, **ERP Submitted Jobs** and **Duplicate Emails**,
   each with a count.
2. Mode tabs ("All" plus one per mode) appear at the top when modes are assigned to you.
3. Use **Quick filter**, **Customer**, **ERP Branch**, **Assigned To** and **Type of shipment** to
   narrow the list. Click a row to open the job in a new tab.

You do not see **✉ Check Email** or **+ New Job** - creating jobs is the operator's work.

### 3. How to review a job
The job screen has the same stages as for the operator. You re-check each one yourself; Gate
Keeper 1's approvals do not count for you.
1. **Required Details Review** - pick each document in the left rail, compare the **Extracted
   Data** with the **Document Preview**, correct anything wrong, then press
   **Approve & Proceed - <document>**. **Undo** reopens a document.
2. **Cross Docs Verification** - check the cross-document comparisons, then press
   **Approved and Proceed**. An open flag does not block you; pressing it accepts the data.
3. **IRN Processing** - press **Approve & Proceed** to mark it complete.
4. **ERP Submission** - the heading reads **Final Approve & Proceed**.

### 4. How to approve
- On ERP Submission press **Final Approve & Proceed**. Cargora prepares the ERP data and starts the
  ERP entry in the background; you are moved back to your list after a few seconds
  (**Go to dashboard now** skips the wait).
- For Excel-entry templates, **Edit ERP Excel** / **Download ERP Excel** let you check the exact
  workbook first. In the Excel editor the submit button reads **Approve & submit to ERP**.
- The bell (top right) tells you when the job is **Submitted to ERP** or **Failed to submit to
  ERP**.

### 5. What if something is wrong (returning a job)
There is no separate "return" button. You have two options:
- **Correct it yourself** on Required Details Review, then approve.
- **Leave it unapproved** and tell the operator what to fix. The job stays pending in your list
  until it is approved.

A Tenant Admin or Super Admin can put a job you already approved back to pending if it has to go
through again.

### 6. How to handle a failed ERP entry
- If the ERP refuses the entry, the job goes to **ERP Failed Jobs** and the button reads
  **Retry & Proceed** (or **Retry & submit to ERP** in the Excel editor).
- The fix card explains what the ERP refused (**You sent** / **ERP needs**). Fix the value, then
  press **Retry**. The documents are not read again - only the entry runs again.
- The operator who owns the job can also correct it and press **Retry & Proceed**.

## Part B - Manager

### 7. What a Manager sees
- Your **Dashboard** says "A read-only view of every job in the tenant": counts and a
  **By User** table (Pending / Completed per user). **Go to Jobs** opens the list.
- You see **every job in the organization** - all templates, all shipment modes, all operators
  and reviewers. No modes or templates are assigned to a Manager.
- The **Workspace** lists and filters are the same as for operators.

### 8. What a Manager cannot do
You can open any job and look at every stage, document and value, but you cannot edit values,
approve, upload or submit. Buttons for those actions are not shown. Ask the operator or Gate
Keeper 2 to act on a job.

## Common questions
**My list is empty.** Either no job has been submitted for approval yet, or no shipment mode is
assigned to you. Ask your Tenant Admin to tick your templates on your user account.

**Why do I have to approve every document again?** Gate Keeper 2 is an independent check; your
approval is recorded separately from Gate Keeper 1's.

**A job went to the ERP without my approval.** Gate Keeper 2 review is switched off for that
shipment type, or Auto ERP Submission is on for that template.

**Can a Manager see another company's jobs?** No - only jobs of their own organization.

**Is there a phone app?** Managers can use the Manager phone app where your company has it.
