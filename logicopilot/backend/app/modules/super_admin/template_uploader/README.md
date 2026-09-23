# Template Reference Uploader

**Module:** Super Admin Panel & Onboarding Wizard
**Duration estimate:** 1 day
**Assignee:** Magesh
**Depends on:** Customer/Tenant Creation (needs a tenant_id)
**Source spec:** `document/implementation_plan.md, Step 3: Reference Document Uploads`

## What this feature does

Tool to upload sample reference documents (BL, Invoice, Packing List) per document type and render multi-page visual previews for the bounding-box step.

## Acceptance criteria

- Super Admin can upload one sample .pdf/.png/.jpg per document type (Invoice, Packing List, BL, Custom).
- Template Name is required (e.g. 'Maersk Bill of Lading v2').
- Multi-page PDFs are converted to page images and are browsable (Next/Prev page).
- Uploaded template is selectable from the Bounding Box Cropper.

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
