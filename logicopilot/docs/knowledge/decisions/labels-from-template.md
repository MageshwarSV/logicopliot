---
title: "Decision: screen labels come only from the template"
category: decisions
module: templates
owner: Senthil
status: ready
summary: Every field label on the job screen is the template's display label - never hardcoded, never from an Excel file.
---
# Decision: screen labels come only from the template

**Date:** 2026-11-28 · **Status:** in force (SIT and production)

## Context
The job screen carried hardcoded lists of field labels in code. A new customer with different
words for the same field needed a code change - against the configurable-engine vision.

## Decision
- The **template's display label** is the only source of a field's screen label.
- Existing hardcoded labels were copied into each template (a one-off script, with a backup and
  a restore option) and then **removed from the code**.
- **No labels from Excel.** New fields are named in the Screen Designer / onboarding.
- A field without a label shows its internal name.

## Not changed (on purpose)
Internal field keys that behaviour depends on, printed E-Docket row labels, and search
placeholders keyed by internal name.

## Consequences
- Renaming a label is a template setting, per environment - not a deploy.
- Developers must **never add a display label in code**.
