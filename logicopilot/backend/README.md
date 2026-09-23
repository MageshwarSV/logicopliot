# Logicopilot backend

FastAPI + SQLAlchemy + Alembic. Local dev runs on SQLite (zero install); production points
`DATABASE_URL` at Postgres instead — same models and migrations, no code changes.

**For full setup, run, test, and deploy instructions (including Windows-specific `uv` PATH
notes), see the root [`README.md`](../README.md).** This file is just a quick command
reference once you've done the one-time setup there.

```bash
uv sync                          # install dependencies (reads pyproject.toml / uv.lock)
uv run alembic upgrade head      # apply DB migrations
uv run python -m app.cli         # seed a Super Admin (first time only)
uv run python -m app.main        # run the API (this is the "run main.py" command)
uv run pytest                    # run the test suite
```
# Option A: after fully closing and reopening VS Code
uv run python -m app.main

# Option B: works immediately, no restart needed
.\run.ps1