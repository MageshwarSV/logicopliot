# Cargora - team rules for every developer and every Claude session

This file is committed: it is the TEAM's rule book. Personal rules (deploy commands, server
access) stay in each person's own, uncommitted notes - never put servers, passwords or keys here.

## Rule 1 - Docs change in the same commit as the code (Knowledge Hub)
Every change to the application updates its Knowledge Hub page in **the same commit**.

- Pages live in `logicopilot/docs/knowledge/<category>/<name>.md` and show in
  Super Admin > Knowledge Hub (header book icon). How to write one:
  `logicopilot/docs/knowledge/admin/writing-docs.md`.
- New feature, changed rule, new setting, new screen: update the page that describes it.
  No page yet: create one, with the header (title, category, module, owner, status, summary).
  A module that is new gets a line in `logicopilot/docs/knowledge/_index.json`.
- Do not hand-write full lists of database tables, API endpoints or settings, or a change
  history - the Hub reads those live from the code. Naming the few settings that change a
  feature on its page is good. Write a clear docstring on the model / endpoint instead.
- Never put passwords, keys, tokens, server addresses or personal contact details in a page.
- `deploy.sh` lists incoming commits that changed the app without a docs page (a warning,
  not a block) - keep that list empty.

## Rule 2 - Configurable, not hardcoded
Cargora is one document-to-system engine configured per customer, document type, rules and
target system (`docs/knowledge/business/product-vision.md`). Prefer template / settings driven
behaviour over customer- or logistics-specific code. Screen labels come from the template's
display_label - never hardcoded, never from an Excel file.

## Rule 3 - Owned areas
The ERP robot (`backend/app/core/browser.py` and the ERP scripts), the job screen
(`frontend/src/pages/jobs/JobRunPage.tsx`), the field editor and Template Settings are owned by
Mageshwar: agree a change with him first.
