---
title: Rapid onboarding - one workflow stream in 2 hours
category: business
module: onboarding
owner: Senthil
status: ready
summary: The north-star goal that makes Cargora scale - onboard a new customer stream by configuration, not code.
---
# Rapid onboarding - the 2-hour goal

## The goal
Given a customer's **ERP Excel template** and **access to their ERP screens**, configure one
workflow stream (Sea Import, Sea Export, Air Import or Air Export) in **about 2 hours**, ready for
a pilot, with minimal developer involvement.

This is our main competitive advantage: every new customer should be faster than the last, and
growth should not need more developers.

## Principles
- **No coding per customer** - customer specifics live in the template and its settings.
- **No repeated work** - fields, prompts, mappings and templates are reused.
- **No hardcoding** - screen labels come from the template; rules are per-template settings.
- **Each customer makes the next one faster** - corrections and mappings are kept.

## The configuration steps
| Step | What is configured |
|---|---|
| 0 Choose template | Start from an existing stream or a blank one |
| 1 ERP field mapping | Upload the ERP Excel; classify every column (from a document, reference data, fixed, operator input, calculated by AI or by rule) |
| 2 Document mapping | Upload sample documents and show where each field sits |
| 3 Screen design | The operator's screen: labels, sections, order |
| 4 Field configuration | How each non-document field gets its value (lookup, fixed, input, AI, formula) |
| 5-6 Settings & go-live | Processing switches, ERP rules, checks on real documents, activate |

The detailed, current flow is in [Customer onboarding](/knowledge/p/workflows/onboarding).

## How we measure it
- Time from kick-off to first real job processed.
- Developer hours spent per new stream (target: close to zero).
- Share of a new stream's settings that came from reuse rather than new work.

## What it means for the team
When you design a feature, ask: **can this be a setting instead of code?** If a customer needs
something different, the answer should be configuration in one of the steps above.
