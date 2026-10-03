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
# connection timed out" and the Jobs list simply failing to load. Widened with real headroom
# to spare, not just enough to limp by: Postgres allows 100 connections total and every OTHER
# app on this box together uses well under half of that.
# SQLite has no such pool to size - its own dialect manages file-handle pooling differently
# and rejects these kwargs outright, so they are Postgres-only.
pool_kwargs = {} if is_sqlite else {"pool_size": 20, "max_overflow": 20, "pool_timeout": 30}
engine = create_engine(settings.database_url, connect_args=connect_args, **pool_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
