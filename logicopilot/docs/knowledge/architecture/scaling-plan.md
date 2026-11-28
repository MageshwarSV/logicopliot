---
title: Scaling plan - from one customer to ten
category: architecture
module: architecture
owner: Senthil
status: ready
summary: The agreed target architecture for about 10 customers and the order of the phases to get there.
---
# Scaling plan - from one customer to ten

## Target
About **10 customers** (a mix of customers whose ERP has an API and customers entered through the
ERP's screens), several mailboxes each, working-hours load of roughly 50,000 jobs and 500,000
pages a month.

## Target shape
| Server | Runs |
|---|---|
| App / API | Website, API, login |
| Workers | Mail intake, OCR, classification, extraction, the large-job lane, the finalizer (the only database writer), API-based ERP entry |
| Database | PostgreSQL with connection pooling, Redis |
| ERP entry | One per customer entered through ERP screens (the robot) |
| Storage | S3 object storage (India region) |
| Monitoring | Health, alerts, backups |

Production today already runs on four servers (application, AI, ERP, database) - see
[Deployment](/knowledge/p/operations/deployment).

## Phases (each one: SIT first, with an off switch, deployed after working hours)
| Phase | What | Why |
|---|---|---|
| 0 | Redis, durable ERP queue, shared ERP login, stuck-job alert, AI cost per job | Stop losing ERP jobs on a restart |
| 1 | Copy files to S3 in the background (local stays main) | Off-server copy of documents |
| 2 | Job queues for mail → classify → extract; workers write results; one finalizer writes the database | Parallel work without database contention |
| 3 | S3 becomes the main store, local disk a cache | Servers stop owning files |
| 4 | Database and Redis on their own server, pooling, point-in-time recovery | Database safety and capacity |
| 5 | Split processes onto the servers above | Scale each part on its own |

Processes are split **before** servers: workers first run as separate processes on the same box.

## Decisions taken
- **Mail intake keeps IMAP** (mailbox polling) until there are 3-4 customers; forwarding
  addresses / an inbound mail server come later - see [decision](/knowledge/p/decisions/imap-for-now).
- **Own LLM: parked.** Hosted models are used; the model is a setting.
- **Large jobs** (big zips) get their own lane so they don't block normal jobs.

## Why
Restarts lost queued ERP jobs, database incidents happened, and very large jobs took over an
hour while blocking others. Each phase removes one of those risks.
