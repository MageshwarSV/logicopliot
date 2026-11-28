---
title: Deployment & operations
category: operations
module: deployment
owner: Senthil
status: ready
summary: Branches, SIT and production, the 4 servers, how a change goes live, backups and rollback.
---
# Deployment & operations

## Branches and environments
| Branch | Runs on | Purpose |
|---|---|---|
| `main` | **SIT** (one server) | Every change goes here first and is checked |
| `production` | **4 production servers** | Only after SIT is verified |

Rollback copy of the old single-server code: branch `single-server-code-backup`.

## The 4 production servers
| Server | Role |
|---|---|
| cargora-app | Website + API (gunicorn/uvicorn workers), migrations run here |
| cargora-ai | Email puller, extraction workers (AI) |
| cargora-erp | ERP robot (Playwright) and its queue |
| cargora-db | PostgreSQL + Redis; nightly backups to S3 |

## How a change goes live
1. Commit on `main` → `git push origin main`.
2. SIT: `./deploy.sh --check` then `./deploy.sh` on the SIT server (pulls, installs, migrates, builds, restarts only `logicopilot-backend` / `logicopilot-frontend`).
3. Check on SIT.
4. Production: merge `origin/main` into `production`, push, then `logicopilot/deploy/deploy-4.sh --check` and `deploy-4.sh`.

Rules: never force-push; never deploy production before SIT; stop if `--check` reports edits made directly on a server; the other pm2 apps on the servers are not ours.

## Configuration that is NOT in git
- `.env` per server (AI model, keys, SMTP, Redis URL …).
- Template / reference-data configuration is changed through the admin screens **per environment** (SIT and production separately).

## Backups
Nightly 11:45 PM: production and SIT databases dumped to S3 (`deploy/backup/cargora-db-backup.sh`). Production keeps 35 daily + 12 monthly copies (locked); SIT keeps one week. Application Health shows the last backup.

## Phone app builds
See `logicopilot/mobile/support-app/README.md` - built with Expo (EAS) in the `axioralabs` account, not deployed to the servers.
