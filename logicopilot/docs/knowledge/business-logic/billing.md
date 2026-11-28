---
title: Billing - page ledger, plans, invoices and SaaS Finance
category: business-logic
module: billing
owner: Senthil
status: ready
summary: How Cargora counts billable pages (PDF and Excel), turns them into a monthly GST tax invoice, and how SaaS Finance, Tenant Admins and payments use it.
---
# Billing - page ledger, plans, invoices and SaaS Finance

## What it does
Cargora bills each customer (tenant) every calendar month for:
- a **platform fee** (monthly, pro-rated in the go-live month), and
- **usage**: every page the AI read, in two price tiers (Tier 1 up to a page limit, Tier 2 beyond).

GST is added and a proper **tax invoice** is raised for each finished month. SaaS Finance (Axiora's
finance team) sees every customer's usage, invoices and payments; a Tenant Admin sees and pays their
own. The actual rates are set per tenant by the Super Admin - they are not written here.

## How it works
1. **A page is read → one ledger row.** At every place pages are read (email-pulled job, manual upload
   or re-upload on Document Capture, Smart Upload, assigning an Unclassified page, Excel intake,
   reclassify), `record_billable_pages` writes a row to `billable_page_events`: tenant, job id, job
   reference, shipment type and page count. A count of 0 writes nothing.
2. **The ledger is permanent.** It has no foreign key to the job and copies the job reference and
   shipment type at write time, so it survives a page or the whole job being deleted later.
3. **Usage is summed from the ledger** (`compute_usage`), never from the documents' current page
   counts - a delete would otherwise erase cost already incurred. Usage is split by shipment type
   and into original vs re-run pages.
4. **The bill** (`calculate_bill`): platform fee × (days live / days in month) + Tier 1 pages × Tier 1
   rate + Tier 2 pages × Tier 2 rate, then GST on the subtotal.
5. **Invoices are raised lazily.** There is no scheduler. `ensure_invoices_finalized` runs whenever
   billing data is opened (Tenant usage page, invoice lists, SaaS Finance Invoices page) and creates
   an invoice for every **fully ended** month that has none yet. The running month is always shown
   live and never invoiced early.
6. **Tax invoice details** (`tax_invoice.py`): numbered per financial year (`<prefix>/2026-27/0001`),
   one series shared with manual invoices. Axiora's details (from Invoice Settings) and the customer's
   details (from their billing details) are **frozen on the invoice** when it is raised. GST is split
   into CGST + SGST (same state) or IGST (different state). Amount in words is in Indian style.
7. **Payment.** An invoice starts **unpaid**. A Tenant Admin can pay online with **Pay now** (Razorpay)
   when the gateway is configured, or by bank transfer; SaaS Finance (or Super Admin) can **mark paid**
   by hand for transfers.

## What counts as a page
- **PDF / images:** every page read, blank pages included, once per pass. A large file read in
  several passes is still one original pass.
- **Re-run:** an admin's explicit reclassify re-reads pages and is billed again, marked as a re-run.
- **Excel sheets** ("Excel by sheet" templates, billed **at intake** in `excel_intake.py`):
  - pages = rows with data ÷ 20, rounded up, **minimum 1** per non-empty sheet
    (`Sheet.billable_pages`, `ROWS_PER_BILLED_PAGE = 20`). Every non-empty row counts, including
    title and address rows;
  - the same whether the sheet lands in a slot or in Unclassified; assigning it from Unclassified
    later is **not** billed again;
  - an **empty sheet is free**;
  - a workbook on the template's **ignore list** (internal sheets like DSRImportTemplate) counts
    **1 page per sheet**, with no AI call;
  - a re-run is billed as a re-run, the same as PDF.

## Business rules
| Rule | Why |
|---|---|
| Every page read is billed, whatever happens to the job later (rejected, duplicate, failed, deleted) | The AI and server cost is spent the moment the page is read |
| Bill from the permanent ledger, not live page counts | Deleting pages or jobs must not erase incurred cost |
| Excel: 20 rows = 1 page, min 1 per non-empty sheet (agreed 2026-11-19, re-confirmed 2026-11-23) | Classification and server work cost money even when a sheet is not extracted. A "40 table rows" variant was tried and dropped - **do not change without the owner's explicit confirmation** |
| "Not Required" (unmatched) pages go through the normal tiers | A separate flat rate was tried on 2026-11-09 and reverted the same day; the columns stay but are always 0 |
| Platform fee pro-rated only in the go-live month (`billing_start_date`) | Go-live on the 15th of a 31-day month bills 17/31; earlier months bill nothing |
| No invoice for months before billing was configured | Never manufacture invoices for history that was never billed |
| A raised invoice keeps the seller/buyer details it was raised with | A tax invoice must not change after issue |
| The server works out the online payment amount | The browser never sends an amount, so it cannot change one |

## Subscription plans and pricing
- Each tenant's numbers live in `tenant_billing_configs` (one row per tenant, created on first save).
  A tenant with no row uses the standard rates from `default_billing_config`.
- The Super Admin edits them (Tenants → billing config). SaaS Finance sees them on **Pricing Plans**,
  read-only by design.
- The same row records which **TrackContainer.in** plan the tenant is on (Starter / Professional,
  with price and container limit). These are stored for reference; the Cargora usage invoice is
  computed from the platform fee, page tiers and GST only.

## Manual invoices
SaaS Finance → Invoices → Manual: one-off invoices. **Draft** (editable, no number) → **Issue** (takes
the next number from the shared series, then locked) → **Paid / Unpaid** → **Cancel** (number kept).
They print with the same tax-invoice layout.

## Online payment (Razorpay)
1. The server creates an order for invoice total + the gateway charge passed on to the tenant (a
   percentage, plus GST on that charge).
2. The browser opens Razorpay's payment window (UPI / card / netbanking).
3. The server checks Razorpay's signature and asks Razorpay what was actually paid.
4. Razorpay's webhook reports the same payment directly (in case the tab is closed); it is applied
   only once (`apply_payment`). Only a Tenant Admin pays, and only their own invoices.
If the gateway keys are not set, the Pay now button is simply not offered.

## Screens
| Who | Screen |
|---|---|
| Tenant Admin / Super Admin | Billing & Usage (`frontend/src/pages/billing/BillingUsagePage.tsx`, per-job table `components/billing/JobBillingTable.tsx`) |
| SaaS Finance | `frontend/src/pages/saas-finance/`: Dashboard (`DashboardPage.tsx`, `BusinessAnalytics.tsx` - revenue net of GST, investor view), Customers (view only), Customer detail, Usage & Billing, Invoices (+ Manual), Invoice print, Payments, Invoice Settings, Pricing Plans (read only), Reports (CSV / Excel export) |

## Where in the code
| What | Where |
|---|---|
| Ledger, usage, bill, invoices | `backend/app/core/billing.py` - `record_billable_pages`, `compute_usage`, `list_job_billing`, `calculate_bill`, `_platform_fee_proration`, `default_billing_config`, `ensure_invoices_finalized` |
| Excel page rule | `core/excel_text.py` (`Sheet.billable_pages`, `ROWS_PER_BILLED_PAGE`), `core/excel_intake.py` |
| Tax invoice | `core/tax_invoice.py` - `financial_year`, `split_tax`, `amount_in_words`, `seller_snapshot`, `buyer_snapshot`, `printable` |
| Manual invoices | `core/manual_invoices.py`, `api/v1/manual_invoices.py` |
| Payments | `core/razorpay_gateway.py`, `api/v1/payments.py`; manual `mark_invoice_paid` in `api/v1/tenants.py` |
| Finance portal | `api/v1/saas_finance.py`, `core/finance_aggregation.py`, `core/finance_analytics.py`, `core/finance_export.py` |
| Tenant billing APIs | `api/v1/tenants.py` (billing-config, billing/usage, billing/jobs, billing/invoices, billing-details) |
| Tables | `billable_page_events`, `tenant_billing_configs`, `invoices`, `invoice_payments`, `invoice_settings`, `manual_invoices` |

## Settings
- Per tenant (`TenantBillingConfig`): `cargora_monthly_fee`, `cargora_tier1_rate_per_page`,
  `cargora_tier1_page_limit`, `cargora_tier2_rate_per_page`, `gst_percent`, `billing_start_date`,
  `trackcontainer_plan` (+ its price / limit fields)
- Invoice Settings (singleton): `number_prefix`, `due_days`, `terms`, `sac_platform`, `sac_usage`,
  seller details, `bank_details`
- Template: `excel_mode`, `excel_ignore_patterns`
- Server config: `razorpay_fee_percent`, `razorpay_fee_gst_percent` (gateway keys are secrets, kept
  out of docs)

## Design decisions
- **Append-only ledger** instead of reading page counts at month end.
- **Lazy month-end invoicing** instead of a scheduler - the codebase has no cron; it is a no-op once
  caught up.
- **SaaS Finance is view-mostly**: it cannot create, edit or delete tenants or change prices; its write
  actions are marking invoices paid, manual invoices, Invoice Settings and each customer's
  billing details (Bill To / Ship To).
