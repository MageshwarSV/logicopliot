from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()

is_sqlite = settings.database_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}

# Default pool_size=5/max_overflow=10 (15 total) proved too tight: the background email
# poller holds ONE connection for its entire cycle - which legitimately runs several minutes
# whenever it finds new mail (several OpenAI calls per document, occasionally with
# rate-limit retries) - and that one long-lived connection was enough to starve ordinary web
# requests under any real concurrent load, surfacing as "QueuePool limit ... reached,
# connection timed out" and the Jobs list simply failing to load. Widened once already to
# 20/20 (40 total) for that reason.
#
# Raised again on 2026-11-03, as one layer of the fix for that day's outage (28 connections
# sat `idle in transaction` against a pool of 40 while every thread waited on OpenAI - see
# extraction_queue.py for the real fix, which caps how many of those can be open at once).
# 30/30 (60 total) is extra cushion on top of that cap, not a replacement for it: Postgres
# itself allows 100 connections total on this box, with 31GB RAM and nothing else of any size
# sharing it, so 60 still leaves 40 spare - do not push this toward 100, that would leave zero
# room for even a single direct psql session.
# SQLite has no such pool to size - its own dialect manages file-handle pooling differently
# and rejects these kwargs outright, so they are Postgres-only.
pool_kwargs = {} if is_sqlite else {"pool_size": 30, "max_overflow": 30, "pool_timeout": 30}
engine = create_engine(settings.database_url, connect_args=connect_args, **pool_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
