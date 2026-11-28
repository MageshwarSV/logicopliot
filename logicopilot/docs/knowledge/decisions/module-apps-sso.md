---
title: "Decision: module apps open in a new tab with the same login"
category: decisions
module: knowledge-hub
owner: Senthil
status: ready
summary: Knowledge Hub (and future module apps) are separate apps inside the platform, opened from header icons, signed in by the shared login cookie.
---
# Decision: module applications with single sign-on

**Date:** 2026-11-28 · **Status:** in force

## Context
Cargora is growing beyond the job workflow: Finance, Production Support and now the Knowledge
Hub. Each needs its own navigation without cluttering the operations sidebar.

## Decision
- Each is a **module application** with its own layout, inside the same web app and domain.
- Opened from **icons in the top header**, in a **new browser tab**.
- **Single sign-on through the existing login cookie** - same domain, so the new tab is already
  signed in; no second login, no token in the URL.
- Access is by role (the Knowledge Hub: Super Admin only), enforced by the backend, not just the menu.

## Rejected
- A separate site / subdomain with its own login - a second login and more to deploy.
- Passing a token in the link - can leak through history and logs.
