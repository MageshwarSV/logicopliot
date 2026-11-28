---
title: "Decision: only official customs tariff codes go to the ERP"
category: decisions
module: cth-tariff
owner: Senthil
status: ready
summary: A global ICEGATE tariff master; the AI suggests a code only from it, and no code outside it can be sent.
---
# Decision: only official customs tariff (CTH) codes

**Date:** 2026-11-26 · **Status:** in force, per-template switch

## Context
A wrong CTH (customs tariff heading) code on a Bill of Entry is a compliance problem. Each
customer also has its own "CTH dump" of codes used before.

## Decision
- One **global tariff master** for all customers, kept separately from each customer's dump
  (the dump logic is unchanged).
- The tariff file has codes without descriptions, so the AI first picks the 8-digit code, then
  chooses among the codes under that 6/4-digit prefix ("option B").
- A document's CTH that is in the tariff is used directly - no AI.
- **Dump or previous-entry codes not in the tariff are not used**; the AI suggests instead.
- The suggestion goes into the CTH box, labelled as an AI suggestion not from the dump.
- **Submission blocks any code outside the tariff.**
- Switched per template (Processing settings); switching it off is the rollback.

## Consequences
The master must be kept up to date by re-uploading the official file.
