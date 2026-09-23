# AI Prompt Training Loop & Correction UI

**Module:** Super Admin Panel & Onboarding Wizard
**Duration estimate:** 1.5 days
**Assignee:** Magesh
**Depends on:** AI Transformation Prompt Editor + Interactive Bounding Box Cropper
**Source spec:** `document/implementation_plan.md, Step 6: Testing & Prompts Training Loop`

## What this feature does

The testing screen where admins review extraction results, correct AI mistakes inline, and click 'Train Data' — the correction is stored as a few-shot example so the system avoids the same mistake next time.

## Acceptance criteria

- Admin sees extracted + cross-checked values in a preview screen.
- If a value is wrong, admin can select the field, type the correct value, and click 'Train Data'.
- Correction is stored (original_value, corrected_value) against the field mapping.
- Training history per field is viewable so admins can see what's already been corrected.

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
