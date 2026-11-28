---
title: How to write and update docs (team rule)
category: admin
module: knowledge-hub
owner: Senthil
status: ready
summary: The rule every developer and every Claude session follows - docs change in the same commit as the code.
---
# How to write and update docs

## The rule
**Every change to the application updates its documentation in the same commit.**
This applies to every developer and every Claude session. A new feature, a changed rule, a new
setting, a new table: the page that describes it changes with it. No page yet? Create one.

## Where pages live
`logicopilot/docs/knowledge/<category>/<name>.md` - in git, next to the code.

Categories: `architecture`, `modules`, `workflows`, `business-logic`, `integrations`,
`operations`, `product`, `user-guides`, `business`, `decisions`, `admin`.

## Page header
```
---
title: ERP entry workflow
category: workflows
module: erp-robot          (a key from docs/knowledge/_index.json)
owner: Mageshwar
status: ready              (draft while incomplete)
summary: One line shown in lists and search.
---
```
"Last updated" and "by" are taken from git automatically - do not type them.

## What a good page has
- **What it does** in plain words, then **how it works** step by step.
- **Business rules and validations**, with the reason behind each.
- **Where in the code** (files / functions) and **settings** that change it.
- **Design decisions**: what was chosen, what was rejected, and why (or a page in `decisions/`).

## What you do NOT write by hand
The Hub reads these live from the code: **database tables**, **API endpoints**, **settings names**
and the **change history**. Write a clear docstring on the model / endpoint instead - it shows up there.

## Completeness
Home shows the share of modules (in `_index.json`) that have at least one `ready` page.
