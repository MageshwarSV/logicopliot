---
title: Database backups
category: operations
module: backups
owner: Senthil
status: ready
summary: Nightly PostgreSQL backups of production and SIT to S3 - schedule, retention, checks, how to restore, open items.
---
# Database backups

## What it does
Every night the **production** and **SIT** databases are copied to Amazon S3. Each copy is
checked, encrypted, and locked so it cannot be deleted before its retention ends. The last
backup's result shows on **Production Support > Application Health** ("Database backup" box).

## Schedule
- Runs from root's cron on each database server at **23:45** (server clock) every day.
- Production runs on the production database server; SIT on the SIT server.
- The script is installed on each server as `cargora-db-backup`; its output goes to a log file on that server.

## How it works (each run)
1. **Dump** the database with `pg_dump` in PostgreSQL's custom (compressed) format, to a local backup folder.
2. **Check** the dump can be read back (`pg_restore --list`) and is not suspiciously small (over about 100 KB). Otherwise the run fails.
3. **Upload** it to S3 under `<env>/db/daily/`, with a SHA-256 checksum, server-side encryption, and an Object Lock (governance mode) until the end of its retention.
4. On the **1st of the month**, also upload a copy under `<env>/db/monthly/` (production only).
5. **Confirm** the size in S3 matches the local dump.
6. **Tidy** the server: keep only the newest few dumps locally (3 by default).
7. **Report** the result (ok or failed, time, file and size) to Redis key `cargora:backup:<env>`. Application Health reads it.

Any failure stops the run, reports "failed" with the reason, and removes the half-written file.
A `--dry-run` option dumps and checks without uploading.

## Retention
| Copy | Kept | Notes |
|---|---|---|
| Production daily | 35 days | locked for 35 days |
| Production monthly (1st of month) | 12 months | moves to a cheaper archive tier (Glacier Instant Retrieval) after 30 days |
| SIT daily | 7 days | no monthly copy |

The S3 lifecycle rules (`prod-daily-35-days`, `prod-monthly-12-months`, `sit-daily-7-days`) delete
copies one day after their lock ends. The bucket has versioning and Object Lock on and public access blocked.

## Health box rules
On Application Health, "Database backup" shows:
- **green** - last backup succeeded within 26 hours;
- **red** - last backup failed, or no successful backup for over 26 hours;
- **amber** - no backup has reported yet;
- **unknown** - no Redis, so the status cannot be read.

The newest report across environments is shown. Reports expire from Redis after 14 days.

## How to restore (generic steps)
Do this with the database owner; never restore over production without agreement.
1. Pick the copy: list the `daily` (or `monthly`) folder for the environment and choose the date.
2. Download it with an AWS user that can read that folder. Copies older than 30 days in `monthly` are in the archive tier but can still be downloaded directly.
3. Check it: `pg_restore --list <file>` should print the table of contents.
4. **Restore into a new, empty database first** (for example a test copy) with `pg_restore` (`--no-owner` if the users differ).
5. Check row counts and recent jobs in the restored copy.
6. Only then switch the app to it, or copy back the tables you need, with the app stopped.

## Security
- Settings (database name, bucket, folder prefix, retention days, Redis address) live in a root-only file on each server. A template is in the repo: `deploy/backup/cargora-backup.env.example`. **The real file is never committed.**
- Each server uploads with its **own AWS user**, allowed to write and read **only its own folder**, with **no delete** permission. Even a stolen server key cannot erase backups.
- The local backup folder is readable by root only.

## Business rules
- **Nightly, not continuous.** Reason: good enough for today's volume; at worst one day of data is at risk.
- **Locked copies.** Reason: protection against accidental deletion or ransomware.
- **Verify before and after upload.** Reason: a backup that cannot be restored is worse than none, because it gives false comfort.
- **Separate folders and users per environment.** Reason: SIT can never touch production backups.

## Open items
- **A full restore test has not been done yet.** Until it is, restores are untested.
- **Uploaded documents are not backed up** - only the database. The files customers send (stored on the app server's disk) need their own backup.
- A Redis password should be rotated (follow-up with the infrastructure owner).

## Where in the code
| Part | Code |
|---|---|
| Backup script | `deploy/backup/cargora-db-backup.sh` - `upload`, `report`, `fail` |
| Settings template | `deploy/backup/cargora-backup.env.example` |
| S3 lifecycle rules | `deploy/backup/s3-lifecycle.json` |
| AWS permissions | `deploy/backup/iam-policy-prod.json`, `deploy/backup/iam-policy-sit.json` |
| Health box | `backend/app/core/support_health.py` - `components` (`backup_box`) |

## Settings
Script settings (names only): `ENV_NAME`, `DB_NAME`, `S3_BUCKET`, `S3_PREFIX`, `DAILY_DAYS`,
`MONTHLY_DAYS`, `KEEP_LOCAL`, `LOCAL_DIR`, `AWS_PROFILE_NAME`, `REDIS_URL`, and
`CARGORA_BACKUP_CONF` (to point the script at a different settings file).

## Design decisions
- **pg_dump custom format** - compressed and lets `pg_restore` pick single tables.
- **Object Lock in governance mode** rather than compliance, so the account owner can still remove a copy in an emergency.
- **Status through Redis**, so the app needs no access to S3 to show backup health.
