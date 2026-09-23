# Interactive Bounding Box Cropper

**Module:** Super Admin Panel & Onboarding Wizard
**Duration estimate:** 1.5 days
**Assignee:** Magesh
**Depends on:** Custom Label Builder + Template Reference Uploader
**Source spec:** `document/implementation_plan.md, Step 4: Preview and Key-Value Tagging`

## What this feature does

The drawing canvas where admins highlight fields using the 5-color system (Red/Green/Blue/Yellow/Purple) to map document data. On crop, the backend runs OCR on the region + surrounding text to auto-detect an anchor term (e.g. 'Seaway Bill No:') and generates a tailored extraction prompt.

## Acceptance criteria

- Admin can draw a bounding box over a value on a template page and link it to an existing Label.
- Box color follows the 5-color system: Red=IDs, Green=amounts, Blue=text, Yellow=dates, Purple=long text.
- On save, backend crops the coordinate area, OCRs it + surrounding text, and stores the detected anchor term.
- A generated_prompt is produced from the anchor term + coordinates for later AI extraction.
- Boxes are stored as normalized (0-1) coordinates so they scale to any image resolution.

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
