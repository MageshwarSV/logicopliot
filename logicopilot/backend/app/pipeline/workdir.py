"""The job's work folder: where pipeline workers keep their results as JSON files.

    uploads/jobs/{job_id}/work/
        manifest.json         files, pages, billing count
        template.json         the template frozen for this job (snapshot.py)
        pages/012.png         page image
        pages/012.ocr.json    OCR result for page 12
        classify.json         documents, types, slots
        docs/007.extract.json fields for document 7
        cost.jsonl            one line per AI call

Every JSON file carries "_v" (SCHEMA_VERSION), so a later change to a format can tell an old
file from a new one instead of misreading it. Writes are atomic (temp file + rename): a worker
killed mid-write leaves the old file or none, never half a file - which is what lets "a result
file exists" mean "this step is done" when work resumes after a restart.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path

from app.core.config import get_settings

SCHEMA_VERSION = 1

_SAFE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_cost_lock = threading.Lock()


def _check_id(job_id: str) -> str:
    """A job id becomes part of a path: refuse anything that could climb out of uploads/."""
    if not isinstance(job_id, str) or not _SAFE_ID.match(job_id):
        raise ValueError(f"not a valid job id: {job_id!r}")
    return job_id


def work_dir(job_id: str) -> Path:
    return Path(get_settings().uploads_dir) / "jobs" / _check_id(job_id) / "work"


def manifest_path(job_id: str) -> Path:
    return work_dir(job_id) / "manifest.json"


def template_path(job_id: str) -> Path:
    return work_dir(job_id) / "template.json"


def classify_path(job_id: str) -> Path:
    return work_dir(job_id) / "classify.json"


def page_image_path(job_id: str, page: int) -> Path:
    return work_dir(job_id) / "pages" / f"{int(page):03d}.png"


def page_ocr_path(job_id: str, page: int) -> Path:
    return work_dir(job_id) / "pages" / f"{int(page):03d}.ocr.json"


def doc_extract_path(job_id: str, doc: int) -> Path:
    return work_dir(job_id) / "docs" / f"{int(doc):03d}.extract.json"


def cost_log_path(job_id: str) -> Path:
    return work_dir(job_id) / "cost.jsonl"


def write_json(path: Path, data: dict) -> None:
    """Atomic: the file is either the old one, absent, or complete - never half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"_v": SCHEMA_VERSION, **data}
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(body, ensure_ascii=False, default=str))
    tmp.replace(path)


def read_json(path: Path) -> dict | None:
    """The file's contents, or None when it does not exist or is not readable JSON."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def done(path: Path) -> bool:
    """A step is done when its result file exists and reads back - the resume test."""
    return read_json(path) is not None


def append_cost(job_id: str, entry: dict) -> None:
    """One line per AI call (model, tokens, rupees, stage, page/doc)."""
    path = cost_log_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"_v": SCHEMA_VERSION, **entry}, ensure_ascii=False, default=str)
    with _cost_lock:
        # A crash can leave the last line cut off with no newline; appending straight after it
        # would glue this entry onto that fragment and lose both. Start a fresh line instead.
        prefix = ""
        try:
            with path.open("rb") as fh:
                fh.seek(0, os.SEEK_END)
                if fh.tell():
                    fh.seek(-1, os.SEEK_END)
                    prefix = "" if fh.read(1) == b"\n" else "\n"
        except OSError:
            pass
        with path.open("a", encoding="utf-8") as fh:
            fh.write(prefix + line + "\n")


def read_costs(job_id: str) -> list[dict]:
    out: list[dict] = []
    try:
        lines = cost_log_path(job_id).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a line cut off by a crash is skipped, never fatal
        if isinstance(row, dict):
            out.append(row)
    return out
