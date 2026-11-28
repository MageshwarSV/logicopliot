"""Knowledge Hub (2026-11-28) - Cargora's single source of truth for how the product works.

Two kinds of knowledge, so the Hub cannot drift from the code:

  WRITTEN pages     docs/knowledge/**/*.md in this repository (next to the code they describe),
                    each opening with a small header:
                        ---
                        title: ERP entry workflow
                        category: workflows        (see CATEGORIES)
                        module: erp                (a key from docs/knowledge/_index.json)
                        owner: Mageshwar
                        status: ready | draft
                        summary: one line shown in lists
                        ---
                    They change in the same commit as the code (the team's rule), and their
                    "last updated" / "by" come from git, not from what someone typed.

  GENERATED pages   read live from the running code - database tables (SQLAlchemy models),
                    API endpoints (FastAPI routes), settings names (never values), and the
                    change history (git log). Always current, nobody maintains them.

Super Admin only (app/api/v1/knowledge.py).
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

DOCS_DIR = Path(__file__).resolve().parents[3] / "docs" / "knowledge"
REPO_DIR = Path(__file__).resolve().parents[3]

CATEGORIES = {
    "architecture": "Architecture",
    "modules": "Modules & Features",
    "workflows": "Workflows",
    "business-logic": "Business Logic & Rules",
    "integrations": "APIs & Integrations",
    "operations": "Deployment & Operations",
    "product": "Product Documentation",
    "user-guides": "User Guides",
    "business": "Business & Marketing",
    "decisions": "Design Decisions",
    "admin": "Admin & Settings",
}

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str, ttl: float, fn):
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    val = fn()
    _cache[key] = (time.time(), val)
    return val


def _git(*args: str, timeout: float = 8.0) -> str:
    try:
        out = subprocess.run(["git", "-C", str(REPO_DIR), *args], capture_output=True, text=True, timeout=timeout)
        return out.stdout if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001 - no git on this machine: the Hub still works, without history
        return ""


# ---------------------------------------------------------------- written pages

def _parse(path: Path) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    meta: dict[str, str] = {}
    body = text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip().lower()] = v.strip().strip('"').strip("'")
        body = m.group(2)
    slug = path.relative_to(DOCS_DIR).with_suffix("").as_posix()
    title = meta.get("title") or next((ln.lstrip("# ").strip() for ln in body.splitlines() if ln.startswith("#")), slug)
    category = meta.get("category") if meta.get("category") in CATEGORIES else "modules"
    return {"slug": slug, "title": title, "category": category, "module": meta.get("module") or "",
            "owner": meta.get("owner") or "", "status": meta.get("status") or "ready",
            "summary": meta.get("summary") or "", "body": body, "path": str(path.relative_to(REPO_DIR))}


def _git_stamps() -> dict[str, tuple[str, str]]:
    """{repo path: (last commit ISO date, author)} for every doc, in one git call."""
    out = _git("log", "--format=@@%cI|%an", "--name-only", "--", "docs/knowledge", timeout=12)
    stamps: dict[str, tuple[str, str]] = {}
    cur = None
    for line in out.splitlines():
        if line.startswith("@@"):
            cur = tuple(line[2:].split("|", 1)) if "|" in line else None
        elif line.strip() and cur and line.strip() not in stamps:
            stamps[line.strip()] = cur  # newest first, so the first hit is the last change
    return stamps


def pages() -> list[dict]:
    def build():
        if not DOCS_DIR.exists():
            return []
        stamps = _git_stamps()
        out = []
        for p in sorted(DOCS_DIR.rglob("*.md")):
            if p.name.startswith("_"):
                continue
            d = _parse(p)
            when, who = stamps.get(d["path"], ("", ""))
            if not when:
                when = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(p.stat().st_mtime))
            d.update({"updated": when, "updated_by": who or d["owner"]})
            out.append(d)
        return out
    return _cached("pages", 60, build)


def page(slug: str) -> dict | None:
    return next((p for p in pages() if p["slug"] == slug), None)


def _index() -> dict:
    try:
        return json.loads((DOCS_DIR / "_index.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def modules() -> list[dict]:
    return list(_index().get("modules") or [])


def quick_links() -> list[dict]:
    return list(_index().get("quick_links") or [])


# ---------------------------------------------------------------- generated: database / APIs / settings

def database() -> list[dict]:
    def build():
        import app.db.base  # noqa: F401 - every model
        from app.db.base_class import Base

        docs = {}
        for mapper in Base.registry.mappers:
            cls = mapper.class_
            doc = (cls.__doc__ or "").strip()
            docs[mapper.local_table.name] = (cls.__name__, " ".join(doc.split())[:600])
        out = []
        for name, table in sorted(Base.metadata.tables.items()):
            cls_name, doc = docs.get(name, ("", ""))
            cols = []
            for c in table.columns:
                fk = next(iter(c.foreign_keys), None)
                cols.append({"name": c.name, "type": str(c.type), "nullable": bool(c.nullable),
                             "pk": bool(c.primary_key), "fk": fk.target_fullname if fk else None})
            out.append({"table": name, "model": cls_name, "doc": doc, "columns": cols})
        return out
    return _cached("database", 600, build)


def apis(app) -> list[dict]:
    """Every endpoint, from the app's public OpenAPI schema (stable across FastAPI versions;
    newer FastAPI nests included routers, so app.routes alone no longer lists them)."""
    def build():
        out = []
        for path, ops in (app.openapi().get("paths") or {}).items():
            seg = path.removeprefix("/api/v1/").strip("/").split("/")[0] or "root"
            for method, op in ops.items():
                if method.upper() in ("HEAD", "OPTIONS") or not isinstance(op, dict):
                    continue
                doc = (op.get("description") or "").strip()
                first = " ".join(doc.split("\n\n")[0].split())[:300] if doc else ""
                out.append({"method": method.upper(), "path": path, "module": seg.replace("-", "_"),
                            "name": op.get("operationId") or "", "summary": first or op.get("summary") or "",
                            "tags": list(op.get("tags") or [])})
        out.sort(key=lambda x: (x["module"], x["path"], x["method"]))
        return out
    return _cached("apis", 600, build)


def settings_list() -> list[dict]:
    """Setting NAMES and types only - never a value (secrets live there)."""
    from app.core.config import Settings, get_settings

    s = get_settings()
    out = []
    for name, field in Settings.model_fields.items():
        val = getattr(s, name, None)
        out.append({"name": name.upper(), "type": getattr(field.annotation, "__name__", str(field.annotation)),
                    "set": val not in (None, "") and val != field.default})
    return out


# ---------------------------------------------------------------- generated: change history

def _module_of(path: str) -> str:
    p = path.replace("\\", "/")
    m = re.search(r"backend/app/(?:api/v1|core|models)/([a-z0-9_]+)\.py$", p)
    if m:
        return m.group(1)
    m = re.search(r"frontend/src/pages/([a-z0-9-]+)/", p)
    if m:
        return m.group(1)
    if "/mobile/" in p:
        return "mobile-app"
    if "/alembic/" in p:
        return "database"
    if "/deploy/" in p or p.endswith("deploy.sh"):
        return "deployment"
    if "/docs/" in p:
        return "docs"
    return "other"


def changes(limit: int = 150) -> list[dict]:
    def build():
        out = _git("log", f"-n{max(1, min(limit, 500))}", "--no-merges", "--format=@@%h|%cI|%an|%s", "--name-only",
                   timeout=15)
        items, cur = [], None
        for line in out.splitlines():
            if line.startswith("@@"):
                parts = line[2:].split("|", 3)
                if len(parts) == 4:
                    cur = {"hash": parts[0], "date": parts[1], "author": parts[2], "subject": parts[3], "files": []}
                    items.append(cur)
            elif line.strip() and cur is not None:
                cur["files"].append(line.strip())
        for it in items:
            mods: list[str] = []
            for f in it["files"]:
                m = _module_of(f)
                if m not in mods:
                    mods.append(m)
            it["modules"] = mods[:6]
            it["docs_changed"] = any("docs/knowledge/" in f for f in it["files"])
            it["code_changed"] = any(re.search(r"logicopilot/(backend/app|frontend/src)/", f) for f in it["files"])
            it["files"] = it["files"][:40]
        return items
    return _cached(f"changes:{limit}", 60, build)


# ---------------------------------------------------------------- home + search

def home(app) -> dict:
    ps = pages()
    mods = modules()
    ready_mods = {p["module"] for p in ps if p["module"] and p["status"] != "draft"}
    documented = sum(1 for m in mods if m.get("key") in ready_mods)
    recent = sorted(ps, key=lambda p: p["updated"], reverse=True)[:8]
    return {
        "counts": {"documents": len(ps), "modules": len(mods), "api_endpoints": len(apis(app)),
                   "db_tables": len(database())},
        "completeness": round(100 * documented / len(mods)) if mods else 0,
        "modules_documented": documented,
        "recent": [{k: p[k] for k in ("slug", "title", "category", "updated", "updated_by")} for p in recent],
        "quick_links": quick_links(),
        "by_category": {c: sum(1 for p in ps if p["category"] == c) for c in CATEGORIES},
    }


def search(app, q: str) -> list[dict]:
    q = (q or "").strip().lower()
    if len(q) < 2:
        return []
    words = [w for w in re.split(r"\s+", q) if w]
    out = []
    for p in pages():
        hay = f"{p['title']} {p['summary']} {p['body']}".lower()
        if all(w in hay for w in words):
            i = p["body"].lower().find(words[0])
            snippet = " ".join(p["body"][max(0, i - 80): i + 160].split()) if i >= 0 else p["summary"]
            score = (5 if q in p["title"].lower() else 0) + hay.count(words[0])
            out.append({"type": "page", "slug": p["slug"], "title": p["title"], "category": p["category"],
                        "snippet": snippet, "score": score})
    for t in database():
        if any(w in t["table"].lower() or w in t["model"].lower() for w in words):
            out.append({"type": "table", "title": t["table"], "snippet": t["doc"][:160], "score": 2})
    for a in apis(app):
        if all(w in f"{a['path']} {a['summary']} {a['module']}".lower() for w in words):
            out.append({"type": "api", "title": f"{a['method']} {a['path']}", "snippet": a["summary"][:160], "score": 1})
    out.sort(key=lambda x: -x["score"])
    return out[:60]
