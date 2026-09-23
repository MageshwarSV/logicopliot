# Customer/Tenant Creation

**Status:** ✅ Implemented (reference example for the other 6 features)
**Module:** Super Admin Panel & Onboarding Wizard
**Duration estimate:** Half day
**Assignee:** Magesh
**Depends on:** None (foundational — every other feature needs a tenant_id)
**Source spec:** `document/implementation_plan.md, Step 1: Customer Creation`

## What this feature does

UI to register a new company (e.g. 4S Logistics) and set their region, base currency, timezone, and branding logo. Creates the tenant record that every other Super Admin feature attaches to.

## Acceptance criteria

- Super Admin can create a tenant with name + primary contact email + region + base currency.
- Region is one of US / EU / ASIA. Base currency is one of USD / EUR / GBP.
- New tenant appears in the tenant list immediately after creation.
- Tenant record is what every other module (labels, templates, operators, etc.) is scoped under.

## What's in this folder

- `models.py` — SQLAlchemy table definition(s).
- `schemas.py` — Pydantic request/response contracts.
- `routes.py` — FastAPI router with real CRUD logic (create/list/get/patch against
  SQLite via `app.core.database.get_db`). No service layer — queries live directly
  in the route handlers since this is a single simple entity; introduce one only if
  a future feature's logic actually needs to be shared.

## How this plugs in

This folder is self-contained. It is wired into the app in exactly one place:
`backend/app/main.py`, where its `router` is imported and included. You should not
need to touch any other feature's folder to build this one — only shared
infrastructure lives in `backend/app/core/`.
