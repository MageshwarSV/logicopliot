"""Parallel extraction by prefetch (Phase 2d): a job's AI calls run side by side BEFORE the
per-document loop, which then runs exactly as before.

run_extraction reads one document after another - page text, the fields call, the table-row
calls - ~30 s per document, so a 10-document job spent ~5 minutes and a 110-document one ~52
(JOB-12B137). The calls themselves are independent; what is NOT safe to parallelise is the rest
of the loop, which reads and writes the job's one database session.

So the loop is left alone. Beforehand:
  1. every page's OCR is fetched side by side into the per-page cache files (no database);
  2. each document's text is assembled one at a time, as before (it saves to the session);
  3. every AI call the loop is going to make is made side by side, and its answer kept in
     memory under the EXACT request (function + every argument);
  4. the loop runs unchanged; its calls go through Prefetch.wrap(), which hands back the kept
     answer for an identical request and otherwise makes the real call.

Same results as before by construction: the loop gets the answer the same request would have
got. A request that differs from what was planned, or whose prefetch failed, simply makes the
real call - exactly as before, with the same errors. The "every field blank -> retry once" rule
calls the original functions, never the kept answer, so a transient failure is still retried.

workers <= 1 (the default) makes wrap() return the original function and plans nothing.
"""
from __future__ import annotations

import copy
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

_gate_lock = threading.Lock()
_gate: threading.BoundedSemaphore | None = None
_gate_size = 0


def _global_gate() -> threading.BoundedSemaphore:
    global _gate, _gate_size
    from app.core.config import get_settings

    size = max(1, int(get_settings().parallel_extract_max_total or 1))
    with _gate_lock:
        if _gate is None or _gate_size != size:
            _gate, _gate_size = threading.BoundedSemaphore(size), size
        return _gate


def workers_for(in_large_lane: bool = False) -> int:
    from app.core.config import get_settings

    base = max(1, int(get_settings().parallel_extract_workers or 1))
    if base <= 1 or not in_large_lane:
        return base
    try:
        from app.db.session import SessionLocal
        from app.models.system_setting import SystemSetting

        db = SessionLocal()
        try:
            row = db.query(SystemSetting.large_job_parallel_calls).first()
        finally:
            db.close()
        return max(1, int(row[0])) if row and row[0] else base
    except Exception:  # noqa: BLE001
        logger.warning("could not read large_job_parallel_calls - using %d", base, exc_info=True)
        return base


def request_key(name: str, args: tuple, kwargs: dict) -> str:
    """Identical request -> identical key (paths and other objects by their text)."""
    return json.dumps([name, list(args), kwargs], sort_keys=True, default=str, ensure_ascii=False)


class Prefetch:
    def __init__(self, workers: int):
        self.workers = max(1, int(workers or 1))
        self._answers: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._fns: dict[str, Callable] = {}
        self.planned: list[tuple[str, tuple, dict]] = []
        self.hits = 0
        self.misses = 0

    @property
    def on(self) -> bool:
        return self.workers > 1

    def wrap(self, name: str, fn: Callable) -> Callable:
        """The function the loop calls. Off: the original itself."""
        self._fns[name] = fn
        if not self.on:
            return fn

        def call(*args, **kwargs):
            key = request_key(name, args, kwargs)
            with self._lock:
                found = key in self._answers
                answer = self._answers.get(key)
                if found:
                    self.hits += 1
                else:
                    self.misses += 1
            if found:
                # A copy: the loop edits rows in place (container numbers, reconciled columns).
                return copy.deepcopy(answer)
            return fn(*args, **kwargs)

        return call

    def plan(self, name: str, *args, **kwargs) -> None:
        """Note a call the loop will make with exactly these arguments."""
        if self.on:
            self.planned.append((name, args, kwargs))

    def run(self, on_done: Callable[[int, int], None] | None = None) -> None:
        """Make every planned call, `workers` at a time; keep the answers. A failed call keeps
        nothing - the loop then makes it itself, with today's error handling. `on_done(done,
        total)` after each call (a progress bar), never from two threads at once."""
        if not self.on or not self.planned:
            return
        seen: set[str] = set()
        todo = []
        for name, args, kwargs in self.planned:
            key = request_key(name, args, kwargs)
            if key not in seen:
                seen.add(key)
                todo.append((key, name, args, kwargs))

        def _one(key, name, args, kwargs):
            try:
                with _global_gate():
                    answer = self._fns[name](*args, **kwargs)
            except Exception:  # noqa: BLE001
                logger.warning("prefetch %s failed - the loop will make this call itself", name,
                               exc_info=True)
                return
            with self._lock:
                self._answers[key] = answer

        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=min(self.workers, len(todo)), thread_name_prefix="xpf") as pool:
            futures = [pool.submit(_one, *t) for t in todo]
            # Collected here, as each finishes: the progress callback runs in this thread only.
            for n, fut in enumerate(as_completed(futures), start=1):
                fut.result()
                _report(on_done, n, len(todo))
        logger.info("parallel extraction: %d AI call(s) prefetched, %d at once, %.1fs",
                    len(todo), min(self.workers, len(todo)), time.monotonic() - started)


def _report(on_done: Callable[[int, int], None] | None, done: int, total: int) -> None:
    if on_done is not None:
        try:
            on_done(done, total)
        except Exception:  # noqa: BLE001 - a progress bar must never stop extraction
            logger.debug("progress callback failed", exc_info=True)


def prefetch_page_ocr(pages: list[tuple[Path, int]], workers: int,
                      get_page_ocr: Callable[[Path, int], dict] | None = None,
                      on_done: Callable[[int, int], None] | None = None) -> None:
    """Fill the per-page OCR cache files side by side, so assembling each document's text
    afterwards reads files instead of waiting on Document AI page after page. No database.
    A page that fails is left alone - text_for_doc then reads it itself and reports it as today."""
    if workers <= 1 or not pages:
        return
    if get_page_ocr is None:
        from app.core.docai import get_page_ocr
    from app.core.parallel_ocr import _global_gate as ocr_gate

    def _one(doc_dir: Path, page: int) -> None:
        try:
            with ocr_gate():
                get_page_ocr(doc_dir, page)
        except Exception:  # noqa: BLE001
            logger.debug("page OCR prefetch failed for %s p%s", doc_dir, page, exc_info=True)

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=min(workers, len(pages)), thread_name_prefix="xocr") as pool:
        futures = [pool.submit(_one, d, p) for d, p in pages]
        for n, fut in enumerate(as_completed(futures), start=1):
            fut.result()
            _report(on_done, n, len(pages))
    logger.info("parallel extraction: %d page(s) of text prefetched, %d at once, %.1fs",
                len(pages), min(workers, len(pages)), time.monotonic() - started)
