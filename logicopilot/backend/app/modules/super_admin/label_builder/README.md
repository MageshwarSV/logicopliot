# Custom Label Builder

**Module:** Super Admin Panel & Onboarding Wizard
**Duration estimate:** 1 day
**Assignee:** Magesh
**Depends on:** Customer/Tenant Creation (needs a tenant_id)
**Source spec:** `document/implementation_plan.md, Step 2: Custom Label Configuration`

## What this feature does

Interface to create data tags (e.g. invoice_no, gross_weight), define their data type, and toggle 'Verify with other document' for cross-doc validation.

## Acceptance criteria

- Super Admin can create unlimited labels per tenant (key-value concept, e.g. `container_number`).
- Each label has a Data Type: String, Number, Date, or Currency.
- Each label has an 'Is Required Field' toggle.
- Each label has a 'Verify with other document' checkbox (Yes/No) — flags it for cross-doc AI verification later.

## What's in this folder

- `models.py` — SQLAlchemy table definition(s).
- `schemas.py` — Pydantic request/response contracts.
- `routes.py` — FastAPI router. Every endpoint currently raises `501 Not Implemented`
  with a pointer back to this README — replace the body with real logic, keep the
  route signatures (path, method, request/response schema) unless the contract
  itself needs to change.

## How this plugs in

This folder is self-contained. It is wired into the app in exactly one place:
`backend/app/main.py`, where its `router` is imported and included. You should not
need to touch any other feature's folder to build this one — only shared
infrastructure lives in `backend/app/core/`.
