---
title: Knowledge Hub
category: modules
module: knowledge-hub
owner: Senthil
status: ready
summary: This Hub - written pages from the repository plus database, APIs, settings and change history read live from the code.
---
# Knowledge Hub

## What it does
The Knowledge Hub is Cargora's **single source of truth** for how the product works. It is a
module application of its own (like Finance and Production Support), for the **Super Admin only**.

It holds two kinds of knowledge:
- **Written pages** - Markdown files in the repository, written by people (like this one).
- **Generated pages** - read live from the running code, so they can never go out of date:
  database tables, API endpoints, settings names and the change history.

## How to open it
- The header of the main app shows a **book icon** for the Super Admin.
- It opens `/knowledge` in a **new browser tab**.
- The login cookie is shared by both tabs, so the Hub is **already signed in** - no second login (single sign-on).
- Any other role does not see the icon, and is sent back to its own home page if it opens the link.

## Sections
| Section | Route | Source |
|---|---|---|
| Home | `/knowledge` | Counts, completeness, recently updated pages, quick links, pages per category |
| Categories | `/knowledge/c/<category>` | Written pages in that category |
| One page | `/knowledge/p/<slug>` | One written page, rendered from Markdown |
| Database | `/knowledge/database` | Every table and column, with the model's description |
| API endpoints | `/knowledge/apis` | Every endpoint, with the first paragraph of its description |
| Settings (.env) | `/knowledge/settings` | Setting names, types and whether each is set - **never values** |
| Change History | `/knowledge/changes` | Recent commits from git, grouped by module, marking which changed docs |
| Search | `/knowledge/search` | Pages, tables and APIs |

## How it works

### Written pages
1. Pages live in `logicopilot/docs/knowledge/<category>/<name>.md`, next to the code.
2. Each starts with a header: `title`, `category`, `module`, `owner`, `status`, `summary` (see "How to write and update docs").
3. The backend reads every `.md` file (files starting with `_` are skipped). A missing or unknown category is treated as `modules`; a missing status as `ready`.
4. **"Last updated" and "by" come from git** (the last commit that touched the file), not from anything typed. Without git the file's date is used.
5. The page list is cached for 60 seconds.
6. The browser renders the Markdown and cleans it (DOMPurify) before showing it.

### Modules and quick links
`docs/knowledge/_index.json` lists the **modules** (key and name) and the Home page **quick links**.
A page's `module` header must be one of these keys.

### Completeness
Home shows the share of modules in `_index.json` that have **at least one page that is not
`draft`**. A module with only draft pages does not count. Example: 20 modules, 10 with a ready
page = 50%.

### Generated pages
- **Database** - from the SQLAlchemy models; a table's description is the model class's docstring.
- **API endpoints** - from the app's OpenAPI schema; the description is the endpoint function's docstring.
- **Settings** - from the `Settings` class in `app/core/config.py`: name, type, and "set" when it differs from the default.
- **Change History** - `git log` (no merges), each commit's files mapped to modules (backend file name, frontend page folder, mobile, database, deployment, docs).
Database and APIs are cached for 10 minutes, change history for 60 seconds.

### Search
Matches every word against page titles, summaries and text, and against table names and API
paths. Title matches rank first; up to 60 results.

## Business rules
- **Super Admin only**, on the server (`require_role(SUPER_ADMIN)`) and on the website (`ProtectedRoute`). Reason: it describes internals, endpoints and settings.
- **Never show setting values.** Reason: secrets live there; only names and "set / not set" are shown.
- **Docs change in the same commit as the code.** Reason: the Hub is only trusted if it is current. See "How to write and update docs".
- **Do not write tables, endpoints, settings or history by hand.** Write a clear docstring instead - it appears in the Hub automatically.
- **No secrets in pages** - no passwords, keys, server addresses or personal contact details.

## Where in the code
| Part | Code |
|---|---|
| Hub logic | `backend/app/core/knowledge.py` - `pages`, `page`, `modules`, `quick_links`, `database`, `apis`, `settings_list`, `changes`, `home`, `search`, `CATEGORIES` |
| API | `backend/app/api/v1/knowledge.py` - `/knowledge/home`, `/pages`, `/pages/{slug}`, `/database`, `/apis`, `/settings`, `/changes`, `/search` |
| Website | `frontend/src/pages/knowledge/KnowledgeHub.tsx` - `KnowledgeHubPage` |
| API client | `frontend/src/api/knowledge.ts` |
| Header icon | `frontend/src/components/AppShell.tsx` - `AppLauncher` |
| Route | `frontend/src/routes/routes.tsx` - `/knowledge/*` |
| Content | `logicopilot/docs/knowledge/` and `_index.json` |

## Settings
None of its own. It needs the docs folder and git on the server for page dates and change history.

## Design decisions
- **Docs in git next to the code**, not in a separate wiki - they are reviewed and deployed with the code they describe.
- **Generated sections instead of hand-written lists** for tables, APIs and settings, so those parts cannot drift.
- **Opens in its own tab with shared-cookie single sign-on**, so it feels like a separate app without a second login.
