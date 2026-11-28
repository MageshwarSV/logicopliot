---
title: "Decision: documentation lives in the code repository"
category: decisions
module: knowledge-hub
owner: Senthil
status: ready
summary: Written pages are Markdown files in git next to the code; technical facts are generated live from the code.
---
# Decision: docs in the repository, facts from the code

**Date:** 2026-11-28 · **Status:** in force

## Context
Documentation in a separate wiki drifts: code changes, the page doesn't, nobody notices.

## Decision
- **Written pages** are Markdown in `logicopilot/docs/knowledge/`, reviewed and deployed with the code.
- **Team rule:** a change updates its page **in the same commit** (`logicopilot/CLAUDE.md`).
- "Last updated" and "by" come **from git**, not typed by hand.
- **Database tables, API endpoints, setting names and change history are generated live** from
  the running code - nobody maintains them.
- `deploy.sh` warns about incoming commits that changed the app without a docs page, and the
  Hub's Change History has a **Missing docs** filter.

## Rejected
- A database-backed wiki edited in the browser - easy to edit, but disconnected from the code
  and its history.

## Consequences
Editing a page is a commit. Non-developers suggest changes to a developer (or a Claude session),
who commits them with the related code.
