"""One-off (2026-11-27): remove the test billing records of customers that never went live.

Bhuvaneswari and ADARSH SHIPPING AND SERVICES never started; every billable page record they
have came from testing and was never invoiced. This deletes those records (billable_page_events)
so no month ever bills them. Jobs, users and templates are NOT touched.

Safe by default:
  - refuses a customer that has any invoice;
  - writes every row it would delete to a JSON backup first;
  - only deletes with --apply.

Run on cargora-app, from the backend folder:
  PYTHONPATH=. venv/bin/python ../deploy/oneoff/remove_test_billing.py            (dry run)
  PYTHONPATH=. venv/bin/python ../deploy/oneoff/remove_test_billing.py --apply    (delete)
"""
import json
import sys
from datetime import datetime, timezone

import app.main  # noqa: F401 - loads every model
from app.db.session import SessionLocal
from app.models.billable_page_event import BillablePageEvent
from app.models.invoice import Invoice
from app.models.tenant import Tenant

CUSTOMERS = ["Bhuvaneswari", "ADARSH SHIPPING AND SERVICES"]
APPLY = "--apply" in sys.argv

db = SessionLocal()
backup = {"taken_at": datetime.now(timezone.utc).isoformat(), "customers": {}}
to_delete = []
for name in CUSTOMERS:
    t = db.query(Tenant).filter(Tenant.name == name).first()
    if t is None:
        sys.exit(f"STOP: no customer named {name!r}")
    if db.query(Invoice).filter(Invoice.tenant_id == t.id).count():
        sys.exit(f"STOP: {name} has an invoice - not touching its billing")
    rows = db.query(BillablePageEvent).filter(BillablePageEvent.tenant_id == t.id).all()
    pages = sum(r.page_count or 0 for r in rows)
    print(f"{name}: {len(rows)} billable page records, {pages} pages (active={t.is_active})")
    backup["customers"][name] = [
        {c.name: (getattr(r, c.name).isoformat() if hasattr(getattr(r, c.name), "isoformat") else getattr(r, c.name))
         for c in BillablePageEvent.__table__.columns}
        for r in rows
    ]
    to_delete += rows

path = f"/root/billing-backup-{datetime.now().strftime('%Y%m%d-%H%M')}.json"
with open(path, "w") as f:
    json.dump(backup, f, indent=1, default=str)
print(f"backup written: {path}")

if not APPLY:
    print("DRY RUN - nothing deleted. Run again with --apply to delete.")
    sys.exit(0)
for r in to_delete:
    db.delete(r)
db.commit()
print(f"deleted {len(to_delete)} records")
