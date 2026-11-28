"""Knowledge Hub API (2026-11-28) - Super Admin only. See app/core/knowledge.py.

  GET /knowledge/home                 counts, completeness, recently updated, quick links
  GET /knowledge/pages?category=      written pages (title, category, module, owner, updated, by)
  GET /knowledge/pages/{slug:path}    one page's text (Markdown)
  GET /knowledge/database             every table and column, read from the models
  GET /knowledge/apis                 every API endpoint, read from the running app
  GET /knowledge/settings             setting names and types (never values)
  GET /knowledge/changes?limit=       change history from git, grouped by module
  GET /knowledge/search?q=            pages, tables and APIs
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from app.core import knowledge as kb
from app.core.deps import require_role
from app.models.user import SUPER_ADMIN, User

router = APIRouter(prefix="/knowledge", tags=["knowledge hub"])
_admin = require_role(SUPER_ADMIN)


@router.get("/home")
def home(request: Request, _u: User = Depends(_admin)) -> dict:
    return {**kb.home(request.app), "categories": kb.CATEGORIES, "modules": kb.modules()}


@router.get("/pages")
def pages(category: str | None = None, _u: User = Depends(_admin)) -> dict:
    items = [{k: v for k, v in p.items() if k != "body"} for p in kb.pages()
             if not category or p["category"] == category]
    return {"items": items, "categories": kb.CATEGORIES}


@router.get("/pages/{slug:path}")
def page(slug: str, _u: User = Depends(_admin)) -> dict:
    p = kb.page(slug)
    if p is None:
        raise HTTPException(status_code=404, detail="No such page")
    return p


@router.get("/database")
def database(_u: User = Depends(_admin)) -> dict:
    return {"tables": kb.database()}


@router.get("/apis")
def apis(request: Request, _u: User = Depends(_admin)) -> dict:
    return {"endpoints": kb.apis(request.app)}


@router.get("/settings")
def settings(_u: User = Depends(_admin)) -> dict:
    return {"settings": kb.settings_list()}


@router.get("/changes")
def changes(limit: int = 150, _u: User = Depends(_admin)) -> dict:
    return {"items": kb.changes(limit)}


@router.get("/search")
def search(request: Request, q: str = "", _u: User = Depends(_admin)) -> dict:
    return {"results": kb.search(request.app, q)}
