---
title: "Decision: check ERP values before sending, stop what the ERP would refuse"
category: decisions
module: erp-rules
owner: Senthil
status: ready
summary: A value that breaks an ERP column rule and cannot be safely converted stops the job before the ERP; Auto ERP still fills empty mandatory values.
---
# Decision: check before the ERP, not after

**Date:** 2026-11-27/28 · **Status:** in force

## Context
An analysis of 60 days of production ERP jobs showed about 15% failing. Most failures were the
robot getting stuck. The rest were data: Excel format / codes, dropdown and address codes,
mandatory pop-ups, credit limits. Country names sent where the ERP wanted ISO codes were a
repeated cause.

## Decision
1. **ERP column rules** (per template, kept by the Super Admin, copy-from-template available)
   check every value before submission. Known conversions are applied (country name → ISO code,
   currency).
2. A value that **breaks a rule and cannot be converted with certainty stops the job before the
   ERP** - Auto ERP included - because the ERP would refuse it anyway. The operator sees the fix card.
3. **Auto ERP keeps filling** empty mandatory values (it does not stop the job for a blank).
4. ERP rejections become **suggested rules** a Super Admin can accept or dismiss.
5. The ERP robot and script code are **not changed** for this work (owned by Mageshwar).

## Rejected
- Stopping Auto ERP for every empty mandatory value - would hold too many good jobs.
- Fixing values inside the robot - risk to a stable, separately owned component.

## Consequences
Failures move from "rejected by the ERP after minutes of robot time" to "shown on the job screen
before submission, with the fix".
