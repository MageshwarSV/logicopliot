---
title: Users, roles and access
category: architecture
module: users-roles
owner: Senthil
status: ready
summary: Every role, who creates it and what it can do; templates vs modes; cookie login, CSRF header and ProtectedRoute.
---
# Users, roles and access

## What it does
Every person who signs in to Cargora has exactly **one role**. The role decides which screens
they see, which customer's data they can touch, and whether they may change anything.
Some roles belong to one customer (tenant); others belong to Axiora and work across all customers.

## The roles
The full list is `ROLES` in `backend/app/models/user.py`.

| Role (code) | Name on screen | Belongs to | Created by | What they can do | Lands on |
|---|---|---|---|---|---|
| `super_admin` | Super Admin | Axiora (no tenant) | - | Everything: customers, templates, onboarding, ERP scripts, rules, settings, users, Knowledge Hub | `/super-admin` |
| `tenant_admin` | Tenant Admin | One customer | Super Admin | The customer's own admin: their users, user types, masters, reports | `/tenant-admin` |
| `operator` | Gate Keeper 1 (Operator) | One customer | Tenant Admin | Works jobs: review, correct, submit; may retry a **failed** ERP run | `/operator` |
| `gk2` | Gate Keeper 2 | One customer | Tenant Admin | Second approval before the ERP, only for the modes (and operators) assigned | `/jobs` |
| `manager` | Manager | One customer | Tenant Admin | Reads every job in their company; **no write action anywhere** | `/manager` |
| `admin` | Admin | Axiora (no tenant) | Super Admin | Jobs across every customer; no customer, template or user management | `/jobs` |
| `saas_finance` | SaaS Finance | Axiora (no tenant) | Super Admin | Every customer's usage, billing and invoices, read-only, plus marking an invoice paid | `/saas-finance` |
| `app_manager` | App login | One customer | Super Admin | The Cargora **Manager phone app** only; reaches no website page; limited to its assigned modes | (refused on the website) |
| `support` | Production Support | Axiora (no tenant) | Super Admin | Every customer's jobs read-only, plus Retry on a failed job; the Support phone app | `/support` |

The cross-tenant roles are `super_admin`, `admin`, `saas_finance`, `support`
(`CROSS_TENANT_ROLES`). The database enforces it: these roles **must** have no tenant, every
other role **must** have one.

### Custom user types
A Tenant Admin can create their own types on **Masters** (for example "Supervisor"). Each type has
a base role (operator, gk2 or manager) and its own **write allowed** switch. The user's real role is
still the base role, so every permission check works unchanged; the type only adds a name and the
write rule. A Tenant Admin can also set operator / gk2 users to read-only for the whole company.

## Templates vs modes - who sees which jobs
Two different fields, often confused:

| | `UserTemplateAssignment` | `User.assigned_modes` |
|---|---|---|
| What | Which **templates** a user may work with | Which **shipment modes** (Air Import, Sea Import ...) |
| Operator | The **real access gate**. No assignment = every template of the company | Only a Jobs-page tab filter |
| Gate Keeper 2 | not used | The **real access gate**. Empty = sees nothing, so at least one is required |
| App login | not allowed | The **real access gate** (at least one required) |
| Manager | not allowed | not allowed - a Manager sees every job |

Gate Keeper 2 has a second, separate narrowing: `assigned_operator_ids` - whose jobs they see.
Empty there means **every** operator (the opposite of modes, so existing accounts kept working).

### New User screen: templates decide modes
Since 28 Nov 2026 the Tenant Admin's **New User** form no longer asks for modes. Every template
belongs to one mode, so:
- **Operator** - tick templates; the modes are worked out from them and saved as the tab filter. Nothing ticked = every template, every tab.
- **Gate Keeper 2** - ticking one template ticks every template of that mode (GK2 reviews by mode). At least one is required.
- **Manager** - no templates or modes.

When editing an existing user, their saved modes pre-tick the matching templates.

## How sign-in works
1. The user signs in on the one login page (`/auth/login`).
2. The server sets two **httpOnly cookies**: a short access token and a refresh token (SameSite=Lax). JavaScript in the page can never read them.
3. When the access token expires, the website quietly calls `/auth/refresh` and retries. Each refresh **replaces** the refresh token, so the session lives as long as the user is active and ends after the idle limit.
4. Phone apps cannot keep browser cookies, so they ask for the tokens in the response and send them as a **Bearer** header. The server reads the cookie first, the Bearer header only when there is no cookie.
5. A user who is deactivated, or whose company is switched off, is refused on the next request, even with a live session.
6. An App login (`app_manager`) is turned away by the website with "open the app on your phone".

### CSRF protection: the X-Requested-With header
Every POST / PUT / PATCH / DELETE must carry the header `X-Requested-With`. Without it the server
answers 403 **"Missing required header"**. A forged form on another website cannot add custom
headers, so this blocks cross-site requests together with the SameSite cookies. The website's
API client and both phone apps always send it. The only exception is the payment provider's
webhook, which proves itself by signature instead.

### Frontend: ProtectedRoute
Every page in `frontend/src/routes/routes.tsx` is wrapped in `ProtectedRoute` with the roles allowed:
- not signed in -> sent to `/login`, remembering where they were going;
- wrong role -> sent to **their own** home page (from `ROLE_HOME`), not an error page;
- `must_change_password` set -> a blocking "change your password" box over the page.

This only hides screens. **The real check is always on the server** (`require_role`).

## Business rules
- **One role per user.** Reason: simple, predictable access.
- **Creation hierarchy**: Super Admin creates tenant admins, app logins, admins, finance and support users; Tenant Admin creates operators, GK2s and managers (or their custom types) in their own company only. Reason: customers manage their own staff; Axiora issues its own and paid logins.
- **A tenant id sent by a Tenant Admin is ignored** - their own tenant is always used. Reason: no way to create users in another company.
- **Manager is read-only everywhere.** Reason: a Manager asks GK1/GK2 to act.
- **GK2 and App logins must have at least one mode.** Reason: the mode list is their only scope; empty would mean nothing visible.
- **A password set by someone else must be changed at first login** (`must_change_password`).
- **Support reads only job pages** through admin-readable endpoints - never users, settings, templates or billing.

## Where in the code
| Part | Code |
|---|---|
| Roles and user table | `backend/app/models/user.py` - `ROLES`, `CROSS_TENANT_ROLES`, `User` |
| Template assignments | `backend/app/models/user_template.py` - `UserTemplateAssignment` |
| Custom user types | `backend/app/models/user_type.py`, `backend/app/api/v1/user_types.py` |
| Create / edit users | `backend/app/api/v1/users.py` - `create_user`, `update_user`, `list_users` |
| Permission checks | `backend/app/core/deps.py` - `get_current_user`, `require_role`, `require_write_access`, `get_tenant_scope`, `scoped_query` |
| Login, refresh, logout | `backend/app/api/v1/auth.py` - `login`, `refresh`, `logout`, `_set_auth_cookies` |
| Tokens | `backend/app/core/security.py` |
| CSRF header check | `backend/app/main.py` - `require_custom_header_on_mutations` |
| Route guard | `frontend/src/auth/ProtectedRoute.tsx`, `frontend/src/auth/roleHome.ts` |
| API client (cookies, header, refresh) | `frontend/src/api/client.ts` |
| New User form (Tenant Admin) | `frontend/src/pages/tenant-admin/CreateUserModal.tsx` |
| Axiora users (Super Admin) | `frontend/src/pages/super-admin/UsersAndAdminsPage.tsx` |

## Settings
`ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_MINUTES` (the refresh one is the real idle
timeout; it must stay longer than the access one), `JWT_SECRET_KEY`, `JWT_ALGORITHM`, `CORS_ORIGINS`.

## Design decisions
- **Cookies, not tokens in browser storage**, so page scripts cannot steal a session.
- **Access token must expire before the refresh token.** When both were set equal, users were logged out every hour mid-job.
- **Rotated refresh tokens get a short grace period in the browser only**, because each job opens in its own tab and tabs refresh at the same moment. Phone apps keep the strict rule.
- **`app_manager` is a separate role from `manager`**, so restricting app logins by mode did not change what existing Managers see.
