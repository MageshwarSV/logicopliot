---
title: How Cargora works (end to end)
category: architecture
module: architecture
owner: Senthil
status: ready
summary: One job from email to ERP entry - every stage, who acts, and where it lives in the code.
---
# How Cargora works (end to end)

Cargora turns shipment documents into validated data and enters it into the customer's ERP.
Today's customer: **4S Logistics** (ERP: LiveImpex / SOFTLINK, ICEGATE Bill of Entry).

```
Email / Upload ─► Classify ─► Extract ─► Review ─► ERP entry ─► Done
```

## 1. Documents arrive
- **Email Puller** (`backend/app/core/email_puller.py`) polls each connected mailbox (Zoho/Gmail) every minute; every pre-alert becomes a **job** of the mailbox's template.
- **Smart Upload** (`jobs.py` → `smart_upload`) does the same for files an operator drops in.
- Zips (also password-protected - `zip_passwords.py`), Excel (`excel_intake.py`), Word (`office_convert.py`) and any image format (`image_convert.py` → PDF) are accepted.
- A pre-alert covering several shipments is split into one job per shipment (`prealert_shipments.py`).

## 2. Classify
Every page is matched to a **document slot** of the template (Invoice, Packing List, Air Waybill, Freight Certificate …) - `classifier_air_import.py` / `classifier_common.py`.

## 3. Extract
The AI (`gpt-6-luna` on production, `OPENAI_MODEL`) reads each document and fills the template's **fields** - header values and **product lines** (`extraction.py`, `parallel_extract.py`). Computed fields follow: CTH from the ICEGATE tariff master, duties, Combined Description, ERP codes.

## 4. Review
- **Gate Keeper 1** (operator) checks every value beside the document and corrects it (job screen, `JobRunPage.tsx`).
- **Gate Keeper 2** approves where the customer wants a second check.
- Cross-document checks flag mismatches (e.g. invoice total vs packing list).

## 5. ERP entry
- `excel_entry.py` builds the **ERP import Excel** (GENERAL, SHIPMENT, INVOICES, ITEMS …) from the template's `excel_config`.
- **Before sending**, `erp_rules.py` checks every column (mandatory, length, lists, country/currency/unit codes, port code by transport mode, field formats). A value the ERP would refuse stops the job with a **Fix card**.
- The **ERP robot** (`browser.py`, Playwright) logs into the ERP, fills the header screens and uploads the Excel. The job gets its ERP job number (e.g. `BLR/AI/4070/26-27`).
- If the ERP refuses, `erp_rejection.py` explains it in plain words; the operator fixes it and presses **Retry**.

## 6. Auto ERP Submission
Per template and branch (today **Bangalore Air**), a job goes to the ERP straight after extraction - no review. Empty mandatory values are filled with the best guess and listed in the job's note (`auto_erp.py`, `auto_erp_complete.py`).

## Who uses it
| Role | What they do |
|---|---|
| Operator (GK1) | Works the job list, reviews, submits |
| Gate Keeper 2 | Approves before the ERP (optional) |
| Manager | Reads everything in their company (+ the Manager phone app) |
| Tenant Admin | The customer's own admin: users, templates, master data |
| Super Admin | Axiora: customers, templates, onboarding, ERP scripts, rules, settings, this Hub |
| Production Support | Axiora: every customer's health and jobs, Retry, notes; Cargora Support phone app |
| SaaS Finance | Axiora: billing, invoices, payments, investor dashboard |

## Where things live
| Area | Code |
|---|---|
| Backend (FastAPI) | `logicopilot/backend/app` - `api/v1/*` endpoints, `core/*` logic, `models/*` tables |
| Database migrations | `logicopilot/backend/alembic/versions` |
| Website (React) | `logicopilot/frontend/src` |
| Support phone app (Expo) | `logicopilot/mobile/support-app` |
| Deployment | `deploy.sh` (SIT), `logicopilot/deploy/deploy-4.sh` (production) |
| This knowledge | `logicopilot/docs/knowledge` |

See also: the **Database** and **APIs** sections of this Hub (read live from the code) and **Change History**.
