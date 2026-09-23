# AI Transformation Prompt Editor

**Module:** Super Admin Panel & Onboarding Wizard
**Duration estimate:** 1 day
**Assignee:** Magesh
**Depends on:** Interactive Bounding Box Cropper (needs a field mapping to attach a prompt to)
**Source spec:** `document/implementation_plan.md, AI Transformation Prompt Editor + Step 6 context`

## What this feature does

Input field (opened via a field's three-dots menu) to write natural language rules for how the extracted value should be formatted, e.g. 'Remove the letters KGS and return only numbers.'

## Acceptance criteria

- Each field mapping can have exactly one transformation prompt (create-or-update / upsert).
- Prompt is free-text natural language, sent to the AI Extraction Engine at run time (that engine is a separate module — this feature only stores the prompt).
- Editing an existing prompt updates it in place and is reflected next extraction run.

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
