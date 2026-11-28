---
title: Super Admin guide
category: user-guides
module: templates
owner: Senthil
status: ready
summary: For Axiora's Super Admin - tenants, templates and their naming standard, onboarding steps, document processing, email puller, platform settings, ERP scripts, system alerts and the Knowledge Hub.
---
# Super Admin guide

## Who this is for
You are a **Super Admin** at Axiora. You create customers (tenants), build and tune the templates
that read their documents, connect mailboxes and keep the platform running. Customers' own admins
(Tenant Admins) manage their users and master data - see the *Tenant Admin guide*.

Your left menu is grouped: **Customers**, **Templates**, **Document Rules**, **Customer
Onboarding**, **ERP & Integrations**, **Operations** (Jobs) and **Finance & System**. A group opens
by itself when you are on one of its pages.

## 1. How to create and manage a tenant
1. Open **Customers → Tenants** (or the **Dashboard**) and press **+ New Tenant**.
2. Fill **Company name**, **Region**, **Currency**, and the first admin's **Admin full name**,
   **Admin email (username)** and **Temporary password**. Press create.
3. Each tenant row has:
   - **Users (n)** - the tenant's accounts; add more with **+ New Tenant Admin** or **+ New App
     Login**.
   - **Generate IRN Link** / **IRN Link** - a standalone, no-login link that shows only this
     tenant's IRN Document Process jobs.
   - **Auto ERP** - allow sending jobs to the ERP straight after extraction, with no review.
     Turning it off stops every template at once; the template choices are kept.
   - **Disable** / **Enable** - switch the customer off without deleting anything.
   - **Delete**.
4. **Customers → Users & Tenant Admins** lists all accounts across tenants.

## 2. How to manage templates (Document Templates)
1. Open **Templates → Document Templates** (page title **Template List**). Filter by **Tenant**.
2. Row actions: **View / Edit**, **Rename**, **Duplicate** (to the same or another tenant) and
   **Delete**.
3. The top of the page shows the **AI model used by every template**. It is set per server, not
   per template.

### Template naming standard
**Rename** checks the new name against the naming standard:
- Starts with the shipment mode (e.g. *Air Import*).
- Title Case, short (40 characters or fewer).
- No dates in the name.
- No customer name - it already shows in the Tenant column.
- No "copy / test / final".
- Not already used by this customer.

Good examples: *Air Import*, *Air Import – Bangalore*, *Sea Import – Excel*, *Sea Export*.

Other pages under **Templates**: **Customer Template Mapping** (the older setup wizard),
**Data Transformation** (review and approve a template's marks and transformations - a template
must be approved before Tenant Admins can assign it to users), **Extraction Corrections** (fields
operators keep correcting, where a prompt needs work) and **Screen Designer**.

## 3. How to onboard a customer (Steps 0-6)
**Customer Onboarding** walks through one template from the customer's ERP Excel and one sample of
each document. Every step saves on the live template. Full detail: *Customer onboarding (Steps
0-6)*.
- **Step 0 - Choose Template**: pick the template, upload the ERP Excel (.xlsx) and one sample per
  document. **Import current setup** fills later steps for a template already on Excel entry.
- **Step 1 - ERP Field Mapping**: sort every ERP column by where its value comes from.
- **Step 2 - Document Mapping**: draw each field's box on the sample documents.
- **Step 3 - Prompts & Test**: tune the extraction prompts and test them.
- **Step 4 - Field Configuration**: field-level settings.
- **Step 5 - Screen Designer**: lay out the job screen once the fields are final.
- **Step 6 - Template Settings & Go Live**: final settings and go live.

## 4. How to set document processing
**Document Rules → Document Processing** - pick a template on the left (or **Find a template…**),
then set:
- **Start extraction automatically** (**Auto**).
- **Hold extraction for a large zip** / **Use the large-job lane** (**Large lane**), with the size
  at which a zip counts as large.
- **Read Excel by sheet**.
- **Excel files not used** - file-name parts that send an Excel file straight to "not used"
  without the AI reading it.
Save, or **Discard** / **Reset to default**. These apply to every job of the template, from email
and Smart Upload alike.

Also under Document Rules: **Indian Customs Tariff (ICEGATE)** (the CTH master for every tenant)
and **Filter Pages** (pages removed from every document before classification, such as a
supplier's terms and conditions).

## 5. How to connect a mailbox (Email Puller)
1. Open **ERP & Integrations → Email Puller** and press **+ Connect mailbox**.
2. Enter the **Mailbox email** and an **App password** created in that mailbox's own account
   settings (Gmail or Zoho).
3. Assign the mailbox a **Tenant** and a **Template**. Mailboxes are independent of any operator.
4. Disconnect a mailbox from its row when it is no longer used.

## 6. ERP scripts (owned by Mageshwar)
**ERP & Integrations → ERP Script Management** holds, per tenant, the recorded steps the ERP robot
replays to enter a job. **+ New ERP Script** names the script, links the templates whose fields
feed that ERP form and opens the recorder; **Mark ready** / **Set draft**, **Duplicate** and
**Delete** manage it. An operator can only be assigned a template that has a ready script.
The robot and its scripts are owned by **Mageshwar** - agree any change with him first.

Related: **ERP Field Configuration** (what each ERP Excel column accepts - checked before anything
is sent) and **Entry Browser**.

## 7. How to use Platform Settings
**Finance & System → Platform Settings** has tabs:
- **Controls** - system-wide switches, e.g. pausing email pull or extraction for every tenant.
- **Workers** - queues and workers per server (waiting, running, oldest waiting, last seen).
- **AI & extraction** - extraction engine, per-tenant AI processing, large jobs.
- **OpenAI keys** - key pool and spend.
- **Pricing** - tenant pricing.
- **Reference data**.
Also in this group: **Billing & Usage** and **API Cost & Usage** (real daily AI cost next to the
pages it paid for).

## 8. System alerts
A banner at the top of every Super Admin screen appears when something needs attention now: the AI
service is unavailable (mail reading paused), a queue's oldest item has waited too long, or a
queue has no worker. It refreshes every 30 seconds and disappears when all is well. Check
**Platform Settings → Workers** for detail.

## 9. Knowledge Hub
The **book icon** in the header opens the **Knowledge Hub** in a new tab, already signed in. It
holds these guides and the team's documentation (**Home**), plus **Database**, **API endpoints**,
**Settings (.env)** and **Change History**, which are read live from the code. Only Super Admins
see the icon.

## Common questions
**Can I edit job values?** Yes - as Super Admin you can correct values on a job, but the job
actions (submit, approve) belong to operators and Gate Keeper 2.

**Why can't a Tenant Admin pick my new template for a user?** It must be approved under **Data
Transformation** first.

**Where do I change the AI model?** It is set per server by the developers, not on a screen.

**Who changes an ERP script?** Mageshwar owns the ERP robot and scripts.
