# Cross-Document Verification Linker

**Module:** Super Admin Panel & Onboarding Wizard
**Duration estimate:** 1 day
**Assignee:** Magesh
**Depends on:** Interactive Bounding Box Cropper (needs field mappings on 2+ documents)
**Source spec:** `document/implementation_plan.md, Step 5: Verification Mapping`

## What this feature does

Left sidebar UI that lets admins link a label's value on Document A to the corresponding target area on Document B, with a condition (Must Equal / Must Be Greater Than / Must Contain) for semantic cross-checking.

## Acceptance criteria

- Only labels marked 'Verify with other document = Yes' show up as linkable in the sidebar.
- Admin selects a previously-cropped label, then draws the matching box on the second document.
- Link is stored as source field mapping -> target field mapping + a condition.
- Verification is semantic, not string-exact (e.g. '1.5 MT' == '1500 KGS', '12-May-2026' == '2026/05/12') — the actual semantic check runs in the AI Extraction Engine module, this feature only captures and stores the link.

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
