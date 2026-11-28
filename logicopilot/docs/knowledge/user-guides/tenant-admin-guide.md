---
title: Tenant Admin guide
category: user-guides
module: users-roles
owner: Senthil
status: ready
summary: For the customer's own administrator - users and access, master data, zip passwords, review rules, dashboards, reports and invoices.
---
# Tenant Admin guide

## Who this is for
You are the **Tenant Admin** - your company's own administrator in Cargora. You set up your team,
keep the master data that fills ERP fields, and follow usage, reports and invoices. You can open
every job of your company, but only to look: operators and Gate Keeper 2 do the editing and
submitting.

Your left menu has three groups: **Billing & Finance**, **Administration** and **Operations**.

## 1. How to add a user (New User)
1. Go to **Administration → Users & Access** and press **+ Add New Member**.
2. In the **New User** window choose the **User type**: **Gate Keeper 1 (Operator)**,
   **Gate Keeper 2**, **Manager**, or one of your custom types. The user type cannot be changed
   after creation.
3. Fill **Full name**, **Email** and a **Temporary password** (at least 8 characters). The user
   changes it after the first login.
4. Pick the templates:
   - **Gate Keeper 1 - "Templates this user works on"**: tick the customer templates this person
     handles. Leave all unticked to allow every template.
   - **Gate Keeper 2 - "Templates this user reviews"**: tick at least one. Jobs of these
     templates waiting for Gate Keeper 2 approval show in this user's queue.
5. **The templates decide the shipment modes.** Every template belongs to one mode (Air Import,
   Air Export, Sea Import, Sea Export). The modes are worked out from the templates you tick and
   shown under the list ("Jobs page tabs: ..." or "Reviews: ..."). For Gate Keeper 2, ticking one
   template ticks every template of the same mode, because Gate Keeper 2 reviews by mode.
6. For Gate Keeper 2 only: **Operators whose jobs this user reviews** starts with everyone
   ticked. Untick an operator to keep their jobs out of this reviewer's queue.
7. A **Manager** needs no templates - a Manager sees every job and cannot change anything.
8. Press **Create user**.

Only templates that are approved show in the list. If you see "No approved templates yet", ask
Cargora support.

## 2. How to change, disable or delete a user
- On **Users & Access**, use the menu on the user's row: **Edit** (then **Save changes**),
  **Disable** / **Enable**, or **Delete**.
- Disable keeps the account and its history; Delete removes the account and its jobs.
- Filter the list by role, **Shipment access** and **Status**, or search by name or email.

## 3. How to set review rules and user types (Masters)
Open **Administration → Master Data → Shipment Type** (the Ports, Consignees and Others links open
the same **Masters** page).
- **User type**: pick a type to see its rules, or create a **New custom type** (for example a
  Supervisor) based on a built-in one, with **Read** or **Write** access, then **Create type**.
- **Gate Keeper 2 review**: by default every job needs Gate Keeper 2 approval before the ERP
  entry. Untick a shipment type to send its jobs straight to the ERP after Gate Keeper 1.
- **Auto ERP Submission**: when Cargora has allowed it for your company, choose the templates whose
  jobs go to the ERP straight after extraction, with no review.
- **Customer reference sheets**: upload or **Replace** a reference sheet per template.
- **Reference values**: for fields marked as target values, download the template, fill it in
  offline and use **Upload filled-in template**.

## 4. How to keep ERP master data
Under **Administration → Master Data**:
- **ERP Master Data – Importer**: importer names, branches (with GSTIN) and addresses used to fill
  the ERP importer fields. Choose the country, then **Add record**, **Edit**, or make a record
  **Inactive** (it is no longer offered on jobs). To change a name or branch, delete the record
  and add a new one.
- **ERP Master Data – Quotation**: quotation names, branches and addresses per template. Same
  actions; to change a name or branch, make the record Inactive and add a new one.
- **CTH Master**: part codes and descriptions with the CTH used on a job, per template. Search,
  **Add record**, edit, download the current file, or **Add Rows (Excel)** from the filled-in
  template. A CTH must be 8 digits. New jobs use your changes straight away.
- **Indian Customs Tariff (ICEGATE)**: the official tariff list the CTH box searches. You can look
  codes up here.
- **Shipments**: your customers. For each one choose which operator handles it and assign a
  **Document mailbox** (**Assign mail** / **Change mail**). **Pull email now** checks the
  mailbox immediately.

## 5. How to save zip passwords
1. Go to **Master Data → Zip Passwords**.
2. Enter a **Label** and the **Password**. Optionally limit it to **Sender domains** or to one
   shipment type.
3. Press **Save password**. Every locked zip that arrives is tried with these automatically.

A job whose zip could not be opened waits with "Waiting for the zip password" until someone types
it on the job. Saving it here avoids that next time.

## 6. How to look at jobs
**Operations → Jobs** opens the jobs workspace in a new tab, with the same lists the operators use
(**New Jobs**, **Missing Documents**, **ERP Failed Jobs**, **ERP Submitted Jobs**, **Duplicate
Emails**) and a tab per shipment mode. Jobs open read-only for you.

## 7. How to read dashboards and reports
- **Dashboard** ("Your organization's jobs, users, and billing, at a glance") shows three areas,
  each opening its own page:
  - **Data Accuracy** - AI accuracy, fields extracted by AI vs manually edited, by document type.
  - **Job Processing Time** - average extraction, review and ERP submission time, end-to-end TAT.
  - **Workforce Productivity** - review hours per employee, average time per job, FTE needed.
- **Operations → Reports → Activity & Billing**: Job Activity, Monthly Usage, Billing Summary,
  Revenue Trend and Outstanding Payments, with **Export CSV** / **Export Excel**.
- **Reports → User Job Summary**: per user, Total / ERP Completed / Pending / Failed jobs for an
  operation and a date range.

## 8. How to see usage and pay invoices
- **Billing & Finance → Usage & Consumption**: pages processed this month by shipment type
  (original and rerun pages) and the charges.
- **Invoices**: every invoice, newest first. **View** opens that month's usage. **Pay now** pays an
  unpaid invoice online where enabled; bank transfer details are shown as well - quote the invoice
  number. The invoice is marked paid once the payment is received.
- **Subscription & Plans**: your current plan, rates and this month's usage against it.

## Common questions
**A new operator sees no jobs.** Check the templates ticked on the user. Jobs also follow the
customer-to-operator assignment under **Shipments**.

**A Gate Keeper 2 sees nothing.** At least one template (and so one mode) must be ticked.

**Can I edit a job's values?** No. Tenant Admins view jobs; operators and Gate Keeper 2 change
them.

**I need a new template or a change to how documents are read.** That is set up by Cargora's
Super Admin - contact Cargora support.
