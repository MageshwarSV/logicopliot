---
title: "Decision: keep mailbox polling (IMAP) until 3-4 customers"
category: decisions
module: email-intake
owner: Senthil
status: ready
summary: Intake reads customers' mailboxes for now; forwarding addresses or our own inbound mail server come later.
---
# Decision: keep IMAP mailbox polling for now

**Date:** 2026-11-20 · **Status:** in force

## Context
Documents arrive by email. Options: poll the customer's mailbox (IMAP with an app password), ask
customers to forward to a Cargora address, or run our own inbound mail server.

## Decision
Keep **IMAP polling** while there are only a few customers. Move to forwarding addresses / an
inbound server after 3-4 customers, and only after the scaling phases that matter more
(queues, storage, database) - see [Scaling plan](/knowledge/p/architecture/scaling-plan).

## Why
It works today, needs nothing from the customer's IT beyond an app password, and the other
phases remove bigger risks first.

## Consequences
Mailbox credentials are stored per mailbox (encrypted, never shown); a mailbox password change on
the customer side stops intake until it is updated.
